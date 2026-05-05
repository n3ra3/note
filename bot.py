import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")

if not API_TOKEN:
    raise ValueError("API_TOKEN is not set in the environment variables.")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

start_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Старт")],
        [KeyboardButton(text="Стоп")],
        [KeyboardButton(text="Часы")],
    ],
    resize_keyboard=True,
)

stop_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Старт")],
        [KeyboardButton(text="Стоп")],
        [KeyboardButton(text="Часы")],
    ],
    resize_keyboard=True,
)


@dataclass
class UserSettings:
    enabled: bool = False
    start_time: time | None = None
    end_time: time | None = None
    last_sent_key: str | None = None


user_settings: dict[int, UserSettings] = {}


def parse_hours(text: str) -> tuple[time, time] | None:
    raw = text.strip()
    if "-" not in raw:
        return None
    start_raw, end_raw = (part.strip() for part in raw.split("-", 1))
    try:
        start_time = datetime.strptime(start_raw, "%H:%M").time()
        end_time = datetime.strptime(end_raw, "%H:%M").time()
    except ValueError:
        return None
    return start_time, end_time


def is_within_hours(now_time: time, start_time: time, end_time: time) -> bool:
    if start_time <= end_time:
        return start_time <= now_time <= end_time
    return now_time >= start_time or now_time <= end_time


async def notification_task() -> None:
    while True:
        now = datetime.now()
        if now.minute in (25, 55):
            minute_key = now.strftime("%Y-%m-%d-%H-%M")
            for user_id, settings in list(user_settings.items()):
                if not settings.enabled:
                    continue
                if settings.start_time and settings.end_time:
                    if not is_within_hours(now.time(), settings.start_time, settings.end_time):
                        continue
                if settings.last_sent_key == minute_key:
                    continue
                try:
                    await bot.send_message(
                        user_id,
                        "Напоминание: 5 минут до следующего получаса.",
                    )
                    settings.last_sent_key = minute_key
                except TelegramNetworkError as exc:
                    logging.error("Network error while sending reminder: %s", exc)
        await asyncio.sleep(30)


async def start_web_server() -> None:
    app = web.Application()

    async def healthcheck(request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app.router.add_get("/", healthcheck)
    port = int(os.getenv("PORT", "8000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


@router.message(Command(commands=["start"]))
async def start_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = True
    await message.reply(
        "Уведомления включены. Нажми 'Часы' и задай рабочее время (HH:MM-HH:MM).",
        reply_markup=stop_keyboard,
    )


@router.message(lambda message: message.text == "Стоп")
async def stop_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = False
    await message.reply("Уведомления остановлены.", reply_markup=start_keyboard)


@router.message(lambda message: message.text == "Старт")
async def restart_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = True
    await message.reply("Уведомления включены.", reply_markup=stop_keyboard)


@router.message(Command(commands=["time"]))
async def time_handler(message: types.Message) -> None:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await message.reply(f"Текущее время сервера: {current_time}")


@router.message(lambda message: message.text == "Часы")
async def set_hours_prompt(message: types.Message) -> None:
    await message.reply("Отправь рабочие часы в формате 24ч: HH:MM-HH:MM")


@router.message(Command(commands=["hours"]))
async def hours_prompt(message: types.Message) -> None:
    await message.reply("Отправь рабочие часы в формате 24ч: HH:MM-HH:MM")


@router.message(lambda message: message.text and "-" in message.text)
async def hours_handler(message: types.Message) -> None:
    parsed = parse_hours(message.text)
    if not parsed:
        await message.reply("Неверный формат. Пример: 09:00-18:00")
        return
    start_time, end_time = parsed
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.start_time = start_time
    settings.end_time = end_time
    await message.reply(
        f"Часы заданы: {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}",
        reply_markup=stop_keyboard if settings.enabled else start_keyboard,
    )


async def main() -> None:
    dp.include_router(router)
    asyncio.create_task(notification_task())
    asyncio.create_task(start_web_server())
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramConflictError:
            logging.error("Another instance of the bot is already running. Exiting...")
            return
        except TelegramNetworkError as exc:
            logging.error("Network error: %s", exc)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logging.info("Bot polling was cancelled. Shutting down gracefully...")
            return
        except Exception as exc:
            logging.error("Unexpected error: %s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
