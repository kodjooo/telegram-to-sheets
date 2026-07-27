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


MAX_RED_LINES = 10
MAX_YELLOW_LINES = 10
MAX_UNRATED_LINES = 5


def _short(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_message(config):
    """Сводка строится из вердиктов триажа (колонки Вердикт/Срочность/Действие
    вкладки Groups): критичное — развёрнуто, шум — одной строкой."""
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
    idx = {name: header.index(name) for name in header if name}

    def col(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and len(row) > i else ""

    act, watch, unrated = [], [], []
    background_logs = 0
    total = 0

    for row in rows[1:]:
        try:
            count = int(col(row, "За 1 день") or 0)
        except ValueError:
            continue
        total += count
        if count == 0:
            continue
        verdict = col(row, "Вердикт").lower()
        entry = {
            "pattern": col(row, "Ошибка (шаблон)"),
            "count": count,
            "urgency": col(row, "Срочность"),
            "action": col(row, "Действие"),
        }
        if verdict == "действовать":
            act.append(entry)
        elif verdict == "понаблюдать":
            watch.append(entry)
        elif verdict == "игнорировать":
            background_logs += count
        else:  # вердикта нет — не оценено
            unrated.append(entry)

    report_date = datetime.now() - timedelta(days=1)
    lines = [f"🧾 Сводка за {report_date.strftime('%d.%m.%Y')} — {total} логов за сутки", ""]

    if act:
        # Один инцидент часто размазан по многим группам (разные джобы, разные
        # SQLSTATE-коды) с одинаковым текстом «Действия» — схлопываем в одну
        # строку с суммарным счётчиком.
        collapsed = {}
        for e in act:
            key = e["action"][:120] or e["pattern"]
            if key in collapsed:
                collapsed[key]["count"] += e["count"]
                collapsed[key]["groups"] += 1
            else:
                collapsed[key] = {**e, "groups": 1}
        act = list(collapsed.values())
        act.sort(key=lambda e: -e["count"])
        lines.append(f"🔴 Действовать ({len(act)}):")
        for e in act[:MAX_RED_LINES]:
            urgency = f" [{e['urgency']}]" if e["urgency"] else ""
            multi = f" (в {e['groups']} группах)" if e.get("groups", 1) > 1 else ""
            lines.append(f"• {_short(e['pattern'], 90)} — {e['count']}/сутки{multi}{urgency}")
            if e["action"]:
                lines.append(f"  ↳ {_short(e['action'], 180)}")
        if len(act) > MAX_RED_LINES:
            lines.append(f"  …и ещё {len(act) - MAX_RED_LINES} (см. таблицу)")
        lines.append("")

    if watch:
        watch.sort(key=lambda e: -e["count"])
        watch_logs = sum(e["count"] for e in watch)
        lines.append(f"🟡 Понаблюдать ({len(watch)} групп, {watch_logs} логов):")
        for e in watch[:MAX_YELLOW_LINES]:
            lines.append(f"• {_short(e['pattern'], 90)} — {e['count']}/сутки")
        if len(watch) > MAX_YELLOW_LINES:
            lines.append(f"  …и ещё {len(watch) - MAX_YELLOW_LINES}")
        lines.append("")

    if unrated:
        unrated.sort(key=lambda e: -e["count"])
        unrated_logs = sum(e["count"] for e in unrated)
        lines.append(f"⚠️ Не оценено триажем ({len(unrated)} групп, {unrated_logs} логов):")
        for e in unrated[:MAX_UNRATED_LINES]:
            lines.append(f"• {_short(e['pattern'], 90)} — {e['count']}/сутки")
        if len(unrated) > MAX_UNRATED_LINES:
            lines.append(f"  …и ещё {len(unrated) - MAX_UNRATED_LINES}")
        lines.append("")

    if not act and not watch and not unrated:
        lines.append("✅ Ничего требующего внимания: весь объём — известный фон.")
        lines.append("")

    if background_logs:
        lines.append(f"⚪ Известный фон (вердикт «игнорировать»): {background_logs} логов")

    lines.append(
        "\n👉 [Подробнее](https://docs.google.com/spreadsheets/d/1eSuLIAlnxkZHA4jy2cBZVwWiA__NZ3pl5hncxU7O3RU/edit?gid=807594473)"
    )
    message = "\n".join(lines)
    # Телеграм ограничивает сообщение 4096 символами
    return message if len(message) <= 4000 else message[:3990] + "\n…"


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
