import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
TZ_NAME = os.getenv("TZ_NAME", "Europe/Chisinau")

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
        [KeyboardButton(text="Часы"), KeyboardButton(text="Убрать часы")],
        [KeyboardButton(text="Пак")],
    ],
    resize_keyboard=True,
)

stop_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Старт")],
        [KeyboardButton(text="Стоп")],
        [KeyboardButton(text="Часы"), KeyboardButton(text="Убрать часы")],
        [KeyboardButton(text="Пак")],
    ],
    resize_keyboard=True,
)

pack_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Пак 5+4+3+2+1"), KeyboardButton(text="Пак 5+3+1")],
        [KeyboardButton(text="Пак 5+3"), KeyboardButton(text="Пак 5+1")],
        [KeyboardButton(text="Пак 5")],
    ],
    resize_keyboard=True,
)


@dataclass
class UserSettings:
    enabled: bool = False
    start_time: time | None = None
    end_time: time | None = None
    reminder_pack: str = "pack_5"
    pending_action: str | None = None
    last_sent_key: str | None = None


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

user_settings: dict[int, UserSettings] = {}


def time_to_str(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def str_to_time(value: str | None) -> time | None:
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def load_user_settings() -> None:
    if not os.path.exists(USERS_FILE):
        return
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("Failed to load users.json: %s", exc)
        return
    for user_id_str, data in raw.items():
        try:
            user_id = int(user_id_str)
        except ValueError:
            continue
        settings = UserSettings(
            enabled=bool(data.get("enabled", False)),
            start_time=str_to_time(data.get("start_time")),
            end_time=str_to_time(data.get("end_time")),
            reminder_pack=str(data.get("reminder_pack", "pack_5")),
            pending_action=None,
            last_sent_key=None,
        )
        if settings.reminder_pack not in PACKS:
            settings.reminder_pack = "pack_5"
        user_settings[user_id] = settings


def save_user_settings() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        str(user_id): {
            "enabled": settings.enabled,
            "start_time": time_to_str(settings.start_time),
            "end_time": time_to_str(settings.end_time),
            "reminder_pack": settings.reminder_pack,
        }
        for user_id, settings in user_settings.items()
    }
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
    except OSError as exc:
        logging.error("Failed to save users.json: %s", exc)


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


PACKS = {
    "pack_5": {
        "label": "Пак 5",
        "minutes": (0, 25, 30, 55),
    },
    "pack_5_1": {
        "label": "Пак 5+1",
        "minutes": (0, 25, 29, 30, 55, 59),
    },
    "pack_5_3": {
        "label": "Пак 5+3",
        "minutes": (0, 25, 27, 30, 55, 57),
    },
    "pack_5_3_1": {
        "label": "Пак 5+3+1",
        "minutes": (0, 25, 27, 29, 30, 55, 57, 59),
    },
    "pack_5_4_3_2_1": {
        "label": "Пак 5+4+3+2+1",
        "minutes": (0, 25, 26, 27, 28, 29, 30, 55, 56, 57, 58, 59),
    },
}

PACK_LABEL_TO_KEY = {pack["label"]: key for key, pack in PACKS.items()}


async def notification_task() -> None:
    while True:
        try:
            now = datetime.now(ZoneInfo(TZ_NAME))
            for user_id, settings in list(user_settings.items()):
                if not settings.enabled:
                    continue
                if settings.start_time and settings.end_time:
                    if not is_within_hours(now.time(), settings.start_time, settings.end_time):
                        continue
                target_minutes = PACKS.get(settings.reminder_pack, PACKS["pack_5"])["minutes"]
                if now.minute not in target_minutes:
                    continue
                minute_key = now.strftime("%Y-%m-%d-%H-%M")
                if settings.last_sent_key == minute_key:
                    continue
                try:
                    if now.minute in (0, 30):
                        current_time = now.strftime("%H:%M")
                        message_text = (
                            f"Уведомляем, что только что наступил {current_time}. "
                            "Ссылки на кейсы доступны ниже:\n"
                            "Kilowatt case - https://pirateswap.com/exchanger?mhn=Kilowatt+Case\n"
                            "Revolution case - https://pirateswap.com/exchanger?mhn=Revolution+Case\n"
                            "Fracture case - https://pirateswap.com/exchanger?mhn=Fracture+Case\n"
                            "Recoil case - https://pirateswap.com/exchanger?mhn=Recoil+Case\n"
                            "Snakebite case - https://pirateswap.com/exchanger?mhn=Snakebite+Case"
                        )
                    elif settings.reminder_pack == "pack_5_4_3_2_1":
                        remaining = 30 - now.minute if now.minute < 30 else 60 - now.minute
                        message_text = f"Напоминание: {remaining} минут до следующего получаса."
                    elif now.minute in (29, 59) and settings.reminder_pack in ("pack_5_1", "pack_5_3_1"):
                        message_text = "Напоминание: 1 минута до следующего получаса."
                    elif now.minute in (27, 57) and settings.reminder_pack in ("pack_5_3", "pack_5_3_1"):
                        message_text = "Напоминание: 3 минуты до следующего получаса."
                    else:
                        message_text = "Напоминание: 5 минут до следующего получаса."
                    await bot.send_message(user_id, message_text)
                    settings.last_sent_key = minute_key
                except TelegramNetworkError as exc:
                    logging.error("Network error while sending reminder: %s", exc)
            next_tick = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            delay = (next_tick - datetime.now(ZoneInfo(TZ_NAME))).total_seconds()
            await asyncio.sleep(max(delay, 0.5))
        except Exception as exc:
            logging.error("Notification loop failed: %s", exc)
            await asyncio.sleep(1)


async def start_web_server() -> None:
    app = web.Application()

    async def healthcheck(request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app.router.add_get("/", healthcheck)
    app.router.add_get("/ping", healthcheck)
    port = int(os.getenv("PORT", "8000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


@router.message(Command(commands=["start"]))
async def start_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = True
    settings.pending_action = None
    save_user_settings()
    await message.reply(
        "Уведомления включены. Нажми 'Часы' и задай рабочее время (HH:MM-HH:MM).",
        reply_markup=stop_keyboard,
    )


@router.message(lambda message: message.text == "Стоп")
async def stop_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = False
    settings.pending_action = None
    save_user_settings()
    await message.reply("Уведомления остановлены.", reply_markup=start_keyboard)


@router.message(lambda message: message.text == "Старт")
async def restart_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.enabled = True
    settings.pending_action = None
    save_user_settings()
    await message.reply("Уведомления включены.", reply_markup=stop_keyboard)


@router.message(Command(commands=["time"]))
async def time_handler(message: types.Message) -> None:
    current_time = datetime.now(ZoneInfo(TZ_NAME)).strftime("%Y-%m-%d %H:%M:%S")
    await message.reply(f"Текущее время сервера ({TZ_NAME}): {current_time}")


@router.message(Command(commands=["status"]))
async def status_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    work_hours = (
        f"{settings.start_time.strftime('%H:%M')}-{settings.end_time.strftime('%H:%M')}"
        if settings.start_time and settings.end_time
        else "не задано"
    )
    pack_label = "Пак 5+1м" if settings.reminder_pack == "extended" else "Пак 5м"
    pack_label = PACKS.get(settings.reminder_pack, PACKS["pack_5"])["label"]
    status_text = "включены" if settings.enabled else "остановлены"
    await message.reply(
        "Текущие настройки:\n"
        f"- Статус: {status_text}\n"
        f"- Рабочие часы: {work_hours}\n"
        f"- Пак: {pack_label}"
    )


@router.message(lambda message: message.text == "Часы")
async def set_hours_prompt(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.pending_action = "work"
    save_user_settings()
    await message.reply("Отправь рабочие часы в формате 24ч: HH:MM-HH:MM")


@router.message(lambda message: message.text == "Убрать часы")
async def clear_hours(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.start_time = None
    settings.end_time = None
    settings.pending_action = None
    save_user_settings()
    await message.reply(
        "Рабочие часы сняты. Уведомления будут приходить всегда.",
        reply_markup=stop_keyboard if settings.enabled else start_keyboard,
    )


@router.message(Command(commands=["hours"]))
async def hours_prompt(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.pending_action = "work"
    save_user_settings()
    await message.reply("Отправь рабочие часы в формате 24ч: HH:MM-HH:MM")


@router.message(lambda message: message.text == "Пак")
async def pack_prompt(message: types.Message) -> None:
    await message.reply("Выбери пак напоминаний:", reply_markup=pack_keyboard)


@router.message(lambda message: message.text in PACK_LABEL_TO_KEY)
async def pack_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    settings.reminder_pack = PACK_LABEL_TO_KEY[message.text]
    save_user_settings()
    await message.reply(
        f"Пак обновлен: {message.text}.",
        reply_markup=stop_keyboard if settings.enabled else start_keyboard,
    )


@router.message(lambda message: message.text and "-" in message.text)
async def hours_handler(message: types.Message) -> None:
    settings = user_settings.setdefault(message.from_user.id, UserSettings())
    parsed = parse_hours(message.text)
    if not parsed:
        await message.reply("Неверный формат. Пример: 09:00-18:00")
        return
    start_time, end_time = parsed
    if settings.pending_action == "work":
        settings.start_time = start_time
        settings.end_time = end_time
        settings.pending_action = None
        save_user_settings()
        await message.reply(
            f"Рабочие часы заданы: {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}",
            reply_markup=stop_keyboard if settings.enabled else start_keyboard,
        )
        return
    await message.reply("Сначала нажми 'Часы'.")


async def main() -> None:
    load_user_settings()
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
