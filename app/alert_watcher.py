"""
Срочные уведомления по критичным логам.

Запускается часто (каждые 1-2 минуты) и работает независимо от telegram_to_sheets.py:
  1. Читает новые сообщения Telegram с момента собственного курсора (alert_last_id.txt).
  2. Сверяет их с критичными триггерами из вкладки Categories (непустой столбец "Алерт").
  3. Совпадения копит в alert_state.json.
  4. Шлёт ОДНО сводное уведомление в alert_chat_id, но не чаще раза в 30 минут,
     сколько бы логов ни падало. Накопленное за период уходит одним сообщением.

Курсор продвигается каждый запуск, поэтому одно событие учитывается один раз.
Сессию используем в режиме только-чтение: копируем в свой /tmp-файл и НЕ копируем обратно,
чтобы не конфликтовать с telegram_to_sheets.py, который сессию персистит.
"""

import os
import json
import asyncio
import logging
import shutil
import sqlite3
import random
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient

from telegram_proxy import get_telegram_proxy
from telegram_to_sheets import prepare_session_paths, CATEGORY_SHEET_TITLE

# ===== Константы =====
BASE_DIR = '/app'
LOG_PATH = os.path.join(BASE_DIR, 'logs/alert_watcher.log')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')
ALERT_LAST_ID_FILE = os.path.join(BASE_DIR, 'alert_last_id.txt')
ALERT_STATE_FILE = os.path.join(BASE_DIR, 'alert_state.json')
TMP_DIR = '/tmp'

# Не чаще одного уведомления в этот интервал, сколько бы логов ни падало.
ALERT_INTERVAL_MIN = 30
# Сколько строк показывать в одном уведомлении (остаток — счётчиком).
MAX_LINES_PER_MESSAGE = 25
# Ограничение на размер накопленного буфера, чтобы не рос бесконечно.
MAX_PENDING = 500
# Сколько последних сообщений тянуть за запуск (страховка от пропусков).
FETCH_LIMIT = 300
TELEGRAM_CONNECT_RETRIES = 4
TELEGRAM_RETRY_DELAYS_SEC = [10, 30, 60]

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# ===== Состояние =====

def read_last_id():
    if os.path.exists(ALERT_LAST_ID_FILE):
        try:
            with open(ALERT_LAST_ID_FILE, 'r') as f:
                return int(f.read().strip())
        except Exception as e:
            logging.error("Ошибка чтения alert_last_id.txt: %s", e)
    return 0


def save_last_id(last_id):
    try:
        with open(ALERT_LAST_ID_FILE, 'w') as f:
            f.write(str(last_id))
    except Exception as e:
        logging.error("Ошибка записи alert_last_id.txt: %s", e)


def read_state():
    """{'last_sent': iso|None, 'pending': [{'trigger': str, 'text': str}, ...]}"""
    if os.path.exists(ALERT_STATE_FILE):
        try:
            with open(ALERT_STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            state.setdefault('last_sent', None)
            state.setdefault('pending', [])
            return state
        except Exception as e:
            logging.error("Ошибка чтения alert_state.json: %s", e)
    return {'last_sent': None, 'pending': []}


def save_state(state):
    try:
        with open(ALERT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        logging.error("Ошибка записи alert_state.json: %s", e)


def can_send(last_sent_iso, now):
    if not last_sent_iso:
        return True
    try:
        last_sent = datetime.fromisoformat(last_sent_iso)
    except Exception:
        return True
    return now - last_sent >= timedelta(minutes=ALERT_INTERVAL_MIN)


# ===== Триггеры =====

def load_alert_triggers():
    """Читает критичные триггеры из вкладки Categories: строки с непустым столбцом C ('Алерт').
    Возвращает список пар (trigger, category) в порядке листа."""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client_gs = gspread.authorize(creds)
    spreadsheet = client_gs.open_by_key(config['google_sheet_id'])
    sheet = spreadsheet.worksheet(CATEGORY_SHEET_TITLE)
    rows = sheet.get_all_values()
    triggers = []
    seen = set()
    for row in rows[1:]:
        if len(row) < 3:
            continue
        category = row[0].strip()
        trigger = row[1].strip().lower()
        is_alert = row[2].strip()
        if trigger and is_alert and trigger not in seen:
            seen.add(trigger)
            triggers.append((trigger, category or trigger))
    return config, triggers


def find_matches(messages, triggers):
    """Список совпадений {category} для сообщений, попавших под критичные триггеры."""
    matches = []
    for m in messages:
        text = (m.message or '').strip()
        if not text:
            continue
        text_lower = text.lower()
        for trigger, category in triggers:
            if trigger in text_lower:
                matches.append({'category': category})
                break
    return matches


def build_alert_text(pending):
    # Уникальные категории в порядке первого появления.
    categories = []
    for item in pending:
        cat = item.get('category') or '(без категории)'
        if cat not in categories:
            categories.append(cat)
    shown = categories[:MAX_LINES_PER_MESSAGE]
    lines = [
        "🤖 Сообщение от бота мониторинга логов",
        "",
        "🚨 Появились критичные логи:",
        "",
    ]
    lines.extend(shown)
    if len(categories) > len(shown):
        lines.append("")
        lines.append(f"…и ещё {len(categories) - len(shown)}")
    return '\n'.join(lines)


# ===== Основная логика =====

async def main():
    client = None
    tmp_session_file = None
    try:
        os.chdir(BASE_DIR)

        # Триггеры и конфиг тянем первыми. Если не вышло — прерываемся БЕЗ продвижения
        # курсора, чтобы следующий запуск переразобрал тот же интервал.
        try:
            config, triggers = load_alert_triggers()
        except Exception as e:
            logging.error("Не удалось загрузить триггеры из Categories: %s", e)
            return

        alert_chat_id = config.get('alert_chat_id')
        if not alert_chat_id:
            logging.warning("alert_chat_id не задан в config.json — уведомления отключены.")
            return
        if not triggers:
            logging.info("Критичных триггеров (столбец 'Алерт') нет — нечего мониторить.")
            return

        session_host_path, tmp_session_name, tmp_session_file = prepare_session_paths(
            config.get('session_name', 'session')
        )
        # Отдельный tmp-файл, чтобы не пересекаться с telegram_to_sheets.py.
        stem = os.path.splitext(os.path.basename(tmp_session_file))[0]
        tmp_session_name = os.path.join(TMP_DIR, f"alert_{stem}")
        tmp_session_file = f"{tmp_session_name}.session"
        if not os.path.exists(session_host_path):
            logging.error("Файл Telegram-сессии %s не найден.", session_host_path)
            return
        shutil.copy2(session_host_path, tmp_session_file)

        client = TelegramClient(
            tmp_session_name,
            config['api_id'],
            config['api_hash'],
            proxy=get_telegram_proxy(config),
        )

        last_connect_error = None
        for attempt in range(1, TELEGRAM_CONNECT_RETRIES + 1):
            try:
                for _ in range(5):
                    try:
                        await client.connect()
                        last_connect_error = None
                        break
                    except sqlite3.OperationalError as e:
                        if 'database is locked' in str(e):
                            await asyncio.sleep(3)
                        else:
                            raise
                else:
                    raise RuntimeError("SQLite-сессия заблокирована.")
                break
            except Exception as e:
                last_connect_error = e
                if attempt == TELEGRAM_CONNECT_RETRIES:
                    break
                delay = TELEGRAM_RETRY_DELAYS_SEC[min(attempt - 1, len(TELEGRAM_RETRY_DELAYS_SEC) - 1)]
                delay += random.uniform(0, 5)
                logging.warning("Не удалось подключиться (попытка %s): %s. Повтор через %.1f с.",
                                attempt, e, delay)
                await asyncio.sleep(delay)
        if last_connect_error is not None:
            logging.error("Не удалось подключиться к Telegram: %s", last_connect_error)
            return
        if not await client.is_user_authorized():
            logging.error("Telegram-сессия не авторизована.")
            return

        last_id = read_last_id()
        messages = await client.get_messages(int(config['chat_id']), limit=FETCH_LIMIT, min_id=last_id)
        new_messages = [m for m in messages if m.id > last_id]

        state = read_state()
        now = datetime.now()

        if new_messages:
            new_messages.sort(key=lambda m: m.id)
            matches = find_matches(new_messages, triggers)
            if matches:
                state['pending'].extend(matches)
                # Защита от бесконечного роста: храним самые свежие.
                if len(state['pending']) > MAX_PENDING:
                    state['pending'] = state['pending'][-MAX_PENDING:]
                logging.info("Найдено критичных: %s (в буфере: %s)", len(matches), len(state['pending']))
            # Курсор двигаем всегда — каждое событие учитываем один раз.
            save_last_id(new_messages[-1].id)

        # Шлём накопленное, но не чаще раза в ALERT_INTERVAL_MIN.
        if state['pending'] and can_send(state.get('last_sent'), now):
            try:
                await client.send_message(int(alert_chat_id), build_alert_text(state['pending']))
                logging.info("Отправлено уведомление: %s логов.", len(state['pending']))
                state['pending'] = []
                state['last_sent'] = now.isoformat()
            except Exception as e:
                # Не очищаем буфер — попробуем в следующий раз.
                logging.error("Не удалось отправить уведомление: %s", e)

        save_state(state)

    except Exception as e:
        logging.error("Ошибка в alert_watcher: %s", e, exc_info=True)
    finally:
        if client is not None:
            await client.disconnect()
        # Сессию НЕ копируем обратно (режим только-чтение).
        if tmp_session_file and os.path.exists(tmp_session_file):
            try:
                os.remove(tmp_session_file)
            except Exception:
                pass


if __name__ == '__main__':
    asyncio.run(main())
