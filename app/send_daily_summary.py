import asyncio
import fcntl
import json
import logging
import os
import random
from collections import defaultdict
from datetime import datetime, timedelta

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
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client_gs = gspread.authorize(creds)
    spreadsheet = client_gs.open_by_key(config["google_sheet_id"])
    sheet = spreadsheet.worksheet("Groups")

    rows = sheet.get_all_values()
    if not rows or len(rows) < 2:
        return "⚠️ Нет данных для формирования отчета."

    header = rows[0]
    category_idx = header.index("Категория")
    count_1d_idx = header.index("За 1 день")

    category_counts = defaultdict(int)
    empty_category_count = 0

    for row in rows[1:]:
        try:
            count = int(row[count_1d_idx])
        except Exception:
            continue

        category = row[category_idx].strip() if len(row) > category_idx else ""
        if category:
            category_counts[category] += count
        else:
            empty_category_count += count

    total = sum(category_counts.values()) + empty_category_count
    report_date = datetime.now() - timedelta(days=1)
    date_str = report_date.strftime("%d.%m.%Y")

    message_lines = [f"🧾 Ежедневная сводка за {date_str} ({total} всего):"]
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        message_lines.append(f"• {cat}: {count}")
    if empty_category_count:
        message_lines.append(f"• Без категории: {empty_category_count}")

    message_lines.append(
        "\n👉 [Подробнее](https://docs.google.com/spreadsheets/d/1eSuLIAlnxkZHA4jy2cBZVwWiA__NZ3pl5hncxU7O3RU/edit?gid=807594473)"
    )
    return "\n".join(message_lines)


async def connect_with_retries(client):
    last_error = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            await client.connect()
            return
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
        session_file = os.path.join(BASE_DIR, config["session_name"])
        telegram_proxy = get_telegram_proxy(config)
        client = TelegramClient(
            session_file,
            config["api_id"],
            config["api_hash"],
            proxy=telegram_proxy,
        )

        try:
            await connect_with_retries(client)
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is not authorized")
            await send_with_retries(client, config["report_channel_id"], message)
        finally:
            if client.is_connected():
                await client.disconnect()

        state["last_sent_date"] = today
        state["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
        logging.info("Сводка за %s успешно отправлена.", today)


if __name__ == "__main__":
    asyncio.run(send_daily_summary())
