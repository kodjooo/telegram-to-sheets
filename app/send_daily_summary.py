import asyncio
import fcntl
import json
import logging
import os
import random
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient

from telegram_proxy import get_telegram_proxy

BASE_DIR = "/app"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "google-credentials.json")
LOG_PATH = os.path.join(BASE_DIR, "logs/daily_summary.log")
STATE_PATH = os.path.join(BASE_DIR, "daily_summary_state.json")
LOCK_PATH = os.path.join(BASE_DIR, "daily_summary.lock")

CONNECT_RETRIES = 6
SEND_RETRIES = 3
RETRY_DELAYS_SEC = [30, 90, 180, 300, 480, 720]

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logging.warning("Не удалось прочитать состояние daily summary: %s", exc)
        return {}


def save_state(state):
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


def build_message(config):
    """Сводка = готовый дайджест, который утренняя рутина триажа пишет во
    вкладку Digest (A1 — дата YYYY-MM-DD, A2 — текст сообщения в Markdown).
    Если свежего дайджеста нет — НЕ отправляем ничего (сознательно без
    фолбэка): пустая сводка хуже отсутствующей, а отсутствие заметно.
    Возвращает None, если отправлять нечего."""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client_gs = gspread.authorize(creds)
    spreadsheet = client_gs.open_by_key(config["google_sheet_id"])
    try:
        digest = spreadsheet.worksheet("Digest")
    except gspread.exceptions.WorksheetNotFound:
        logging.error("Вкладка Digest не найдена — рутина триажа ещё не писала дайджест.")
        return None

    digest_date = (digest.acell("A1").value or "").strip()
    text = (digest.acell("A2").value or "").strip()
    today = datetime.now().strftime("%Y-%m-%d")
    if not text or digest_date != today:
        logging.error(
            "Свежего дайджеста нет (дата в Digest: %r, сегодня %s) — сводка не отправлена.",
            digest_date, today,
        )
        return None
    return text[:4000]


async def connect_with_retries(session_file, api_id, api_hash, proxy):
    """Подключается, пересоздавая клиента на каждой попытке: при сбое прокси-пул
    отдаёт другой upstream, иначе ретраи залипают на одном мёртвом прокси.
    Возвращает подключённого клиента."""
    last_error = None
    client = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        client = TelegramClient(session_file, api_id, api_hash, proxy=proxy)
        try:
            await client.connect()
            return client
        except Exception as exc:
            last_error = exc
            if attempt == CONNECT_RETRIES:
                break
            delay = RETRY_DELAYS_SEC[min(attempt - 1, len(RETRY_DELAYS_SEC) - 1)] + random.uniform(0, 15)
            logging.warning(
                "Не удалось подключиться к Telegram, попытка %s/%s: %s. Повтор через %.1f сек.",
                attempt,
                CONNECT_RETRIES,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_error


async def send_with_retries(client, report_channel_id, message):
    last_error = None
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            await client.send_message(
                report_channel_id,
                message,
                parse_mode="markdown",
                link_preview=False,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt == SEND_RETRIES:
                break
            delay = 10 * attempt + random.uniform(0, 5)
            logging.warning(
                "Не удалось отправить сообщение, попытка %s/%s: %s. Повтор через %.1f сек.",
                attempt,
                SEND_RETRIES,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_error


async def send_daily_summary():
    with open(LOCK_PATH, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info("Другой процесс send_daily_summary.py уже выполняется, выходим.")
            return

        config = load_config()
        today = datetime.now().strftime("%Y-%m-%d")
        state = load_state()
        if state.get("last_sent_date") == today:
            logging.info("Сводка за %s уже отправлена, повтор не нужен.", today)
            return

        message = build_message(config)
        if message is None:
            # Свежего дайджеста нет — не отправляем и НЕ помечаем день
            # отправленным: следующее крон-окно попробует снова.
            return
        session_file = os.path.join(BASE_DIR, config["session_name"])
        telegram_proxy = get_telegram_proxy(config)
        client = None

        try:
            client = await connect_with_retries(
                session_file, config["api_id"], config["api_hash"], telegram_proxy
            )
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is not authorized")
            await send_with_retries(client, config["report_channel_id"], message)
        finally:
            if client is not None and client.is_connected():
                await client.disconnect()

        state["last_sent_date"] = today
        state["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
        logging.info("Сводка за %s успешно отправлена.", today)


if __name__ == "__main__":
    asyncio.run(send_daily_summary())
