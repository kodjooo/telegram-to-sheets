import os
import hashlib
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import re
import random
import functools
import sqlite3
import shutil

import gspread
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as GoogleCreds
from google.auth.exceptions import TransportError as GoogleTransportError
from oauth2client.service_account import ServiceAccountCredentials
from requests import exceptions as requests_exceptions
from telethon import TelegramClient

from telegram_proxy import get_telegram_proxy

# Константы
BASE_DIR = '/app'
LOG_PATH = os.path.join(BASE_DIR, 'logs/telegram_to_sheets.log')
LAST_ID_FILE = os.path.join(BASE_DIR, 'last_message_id.txt')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')
TMP_DIR = '/tmp'
TELEGRAM_CONNECT_RETRIES = 6
TELEGRAM_RETRY_DELAYS_SEC = [30, 90, 180]

# Логирование
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ===== Утилиты =====

def clean_old_logs(log_path, days=7):
    if not os.path.exists(log_path):
        return
    cutoff = datetime.now() - timedelta(days=days)
    new_lines = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                log_date_str = line.split(' - ')[0]
                log_date = datetime.strptime(log_date_str, '%Y-%m-%d %H:%M:%S,%f')
                if log_date > cutoff:
                    new_lines.append(line)
            except Exception:
                new_lines.append(line)
    with open(log_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

def read_last_id():
    if os.path.exists(LAST_ID_FILE):
        try:
            with open(LAST_ID_FILE, 'r') as f:
                return int(f.read().strip())
        except Exception as e:
            logging.error(f"Ошибка чтения last_message_id.txt: {e}")
    return 0

def save_last_id(last_id):
    try:
        with open(LAST_ID_FILE, 'w') as f:
            f.write(str(last_id))
    except Exception as e:
        logging.error(f"Ошибка записи last_message_id.txt: {e}")

def clean_log(text):
    if not text:
        return ''
    # Удаляем только временные метки (например: [2025-06-09T13:59:20.317859 03:00])
    text = re.sub(r'^\[.*?\]\s*', '', text).strip()
    return text


# Нормализация вынесена в normalize.py: обезличиваются ВСЕ логи,
# различительные токены (SQLSTATE-коды, классы, HTTP-статусы) сохраняются.
from normalize import merge_fragment_chains, normalize_error_pattern  # noqa: E402

GROUPS_HEADER = [
    "ID", "Категория", "Ошибка (шаблон)",
    "За 1 день", "За 7 дней", "За 30 дней", "Последнее появление",
    "Статус", "Вердикт", "Срочность", "Причина", "Действие", "Оценено",
]


def group_id(pattern: str) -> str:
    """Стабильный ID группы: не зависит от позиции строки и пересортировок."""
    return hashlib.sha1(pattern.encode('utf-8')).hexdigest()[:8]
ARCHIVE_SHEET_TITLE = 'Archive'
RETENTION_DAYS = 90  # группы без появлений дольше этого срока уезжают в Archive

CATEGORY_SHEET_TITLE = 'Categories'
# Третий столбец "Алерт" читается отдельным alert_watcher.py для срочных уведомлений.
CATEGORY_HEADER = ["Категория", "Триггер", "Алерт"]

def prepare_session_paths(raw_session_name: str) -> tuple[str, str, str]:
    """
    Возвращает путь к файлу сессии на хосте, имя временной сессии в контейнере
    и полный путь к временному .session файлу.
    """
    session_name = (raw_session_name or 'session').strip() or 'session'
    has_extension = session_name.endswith('.session')
    session_file = session_name if has_extension else f"{session_name}.session"
    if not os.path.isabs(session_file):
        host_path = os.path.join(BASE_DIR, session_file)
    else:
        host_path = session_file

    stem = os.path.splitext(os.path.basename(session_file))[0]
    tmp_session_name = os.path.join(TMP_DIR, stem)
    tmp_session_file = f"{tmp_session_name}.session"
    return host_path, tmp_session_name, tmp_session_file


async def update_range(spreadsheet, range_name: str, values: list[list[str]]):
    """Обновление диапазона через Google Sheets API без deprecated предупреждений gspread."""
    await retry_gspread(
        spreadsheet.values_update,
        range_name,
        params={'valueInputOption': 'RAW'},
        body={'values': values}
    )

def extract_category(error_pattern: str, category_rules: dict | None = None) -> str:
    pattern_lower = error_pattern.lower()
    if category_rules:
        for category, triggers in category_rules.items():
            if any(trigger in pattern_lower for trigger in triggers):
                return category
    """
    Извлекает категорию по шаблону:
    production.WARNING: SYNC: ...  → SYNC
    production.ERROR: SQLSTATE[...] ... → SQLSTATE
    production.ERROR: DEBUG: ... → DEBUG
    Возвращает пустую строку, если не удалось точно определить.
    """
    # Явный формат: production.TYPE: CATEGORY:
    match = re.search(r'production\.\w+:\s+([A-Z_]+):', error_pattern)
    if match:
        category = match.group(1)
        # Отфильтровываем только системные слова типа ERROR и WARNING
        if category not in ('ERROR', 'WARNING', 'INFO'):  # DEBUG оставляем
            return category

    # Альтернатива: SQLSTATE[...] → SQLSTATE
    match_sql = re.search(r'\b(SQLSTATE)\b', error_pattern)
    if match_sql:
        return match_sql.group(1)

    # Иначе не указываем категорию
    return ''

def class_name_to_path(class_name: str) -> str:
    if not class_name.startswith("App\\"):
        return ""
    relative_path = class_name.replace("App\\", "").replace("\\", "/")
    return f"app/{relative_path}.php"

def extract_error_and_address(text):
    cleaned_text = clean_log(text)
    address = ''

    # Ищем путь строго начиная с /var/www/app.sellerdata.ru/app/ и заканчивающийся .php:номер
    match = re.search(r'at\s+(/var/www/app\.sellerdata\.ru/app/[^\s:]+\.php):(\d+)', text)
    if match:
        full_path = match.group(1)
        line_number = match.group(2)
        relative_path = full_path.replace('/var/www/app.sellerdata.ru/', '')
        address = f'{relative_path}:{line_number}'

    return cleaned_text.strip(), address

TRANSIENT_HTTP_STATUSES = {408, 409, 429, 500, 502, 503, 504}


def _calc_sleep(current_delay, status=None, base_jitter=2, min_quota_wait=120):
    if status == 429:
        return min_quota_wait
    return current_delay + random.uniform(0, base_jitter)


async def retry_gspread(func, *args, retries=5, delay=3, backoff=2, **kwargs):
    current_delay = delay
    for attempt in range(retries):
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        except gspread.exceptions.WorksheetNotFound:
            raise
        except gspread.exceptions.APIError as e:
            status = None
            if hasattr(e, 'response') and e.response is not None:
                status = getattr(e.response, 'status', None) or getattr(e.response, 'status_code', None)
            if status is None:
                match = re.search(r'\[(\d{3})\]', str(e))
                if match:
                    status = int(match.group(1))
            message = str(e).lower()
            is_transient = (
                (status in TRANSIENT_HTTP_STATUSES) or
                ('temporarily unavailable' in message) or
                ('internal error encountered' in message) or
                ('operation was aborted' in message)
            )
            if not is_transient or attempt == retries - 1:
                raise
            logging.warning("gspread transient error (%s) on attempt %s/%s: %s", status, attempt + 1, retries, e)
            await asyncio.sleep(_calc_sleep(current_delay, status=status))
            current_delay *= backoff
        except (GoogleTransportError, requests_exceptions.RequestException, OSError) as e:
            if attempt == retries - 1:
                raise
            logging.warning("Network error on attempt %s/%s for %s: %s", attempt + 1, retries, func.__name__, e)
            await asyncio.sleep(_calc_sleep(current_delay))
            current_delay *= backoff
        except Exception as e:
            if attempt == retries - 1:
                raise
            logging.warning("Unexpected error on attempt %s/%s for %s: %s", attempt + 1, retries, func.__name__, e)
            await asyncio.sleep(_calc_sleep(current_delay))
            current_delay *= backoff


async def retry_google_api(api_call, retries=5, delay=3, backoff=2):
    current_delay = delay
    for attempt in range(retries):
        try:
            return api_call()
        except HttpError as e:
            if e.resp.status not in TRANSIENT_HTTP_STATUSES or attempt == retries - 1:
                raise
            logging.warning("Google API transient HttpError %s on attempt %s/%s", e.resp.status, attempt + 1, retries)
            await asyncio.sleep(_calc_sleep(current_delay, status=e.resp.status))
            current_delay *= backoff
        except (GoogleTransportError, requests_exceptions.RequestException, OSError) as e:
            if attempt == retries - 1:
                raise
            logging.warning("Google API network error on attempt %s/%s: %s", attempt + 1, retries, e)
            await asyncio.sleep(_calc_sleep(current_delay))
            current_delay *= backoff
        except Exception as e:
            if attempt == retries - 1:
                raise
            logging.warning("Unexpected Google API error on attempt %s/%s: %s", attempt + 1, retries, e)
            await asyncio.sleep(_calc_sleep(current_delay))
            current_delay *= backoff


async def load_category_rules(spreadsheet):
    try:
        sheet = await retry_gspread(spreadsheet.worksheet, CATEGORY_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        sheet = await retry_gspread(
            spreadsheet.add_worksheet,
            title=CATEGORY_SHEET_TITLE,
            rows='200',
            cols='2'
        )
        await update_range(spreadsheet, f"{CATEGORY_SHEET_TITLE}!A1:B1", [CATEGORY_HEADER])

    rows = await retry_gspread(sheet.get_all_values)
    if not rows:
        await update_range(spreadsheet, f"{CATEGORY_SHEET_TITLE}!A1:B1", [CATEGORY_HEADER])
        rows = [CATEGORY_HEADER]

    rules = defaultdict(list)
    for row in rows[1:]:
        if len(row) < 2:
            continue
        category = row[0].strip()
        trigger = row[1].strip().lower()
        if not category or not trigger:
            continue
        if trigger not in rules[category]:
            rules[category].append(trigger)

    return rules


def count_and_aggregate(logs):
    now = datetime.now(timezone.utc)
    error_data = defaultdict(lambda: {
        'counts': {'1d': 0, '7d': 0, '30d': 0},
        'last_seen': None
    })
    # Склеиваем цепочки разрезанных сообщений (голова + хвосты)
    for log in merge_fragment_chains(logs):
        raw_text, _address = extract_error_and_address(log['text'])
        error_pattern = normalize_error_pattern(raw_text)
        if not error_pattern:
            continue
        data = error_data[error_pattern]
        delta = now - log['date'].replace(tzinfo=timezone.utc)
        if delta <= timedelta(days=1):
            data['counts']['1d'] += 1
        if delta <= timedelta(days=7):
            data['counts']['7d'] += 1
        if delta <= timedelta(days=30):
            data['counts']['30d'] += 1
        if data['last_seen'] is None or log['date'].astimezone(timezone.utc) > data['last_seen']:
            data['last_seen'] = log['date'].astimezone(timezone.utc)
    return error_data

# ===== Основная логика =====

async def main():
    config = {}
    client = None
    session_host_path = None
    tmp_session_file = None
    try:
        os.chdir(BASE_DIR)
        clean_old_logs(LOG_PATH, days=7)
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        session_host_path, tmp_session_name, tmp_session_file = prepare_session_paths(config.get('session_name', 'session'))
        if not os.path.exists(session_host_path):
            logging.error(
                "Файл Telegram-сессии %s не найден. "
                "Запустите `docker exec telegram-to-sheets-app python -m telethon.sessions` для авторизации.",
                session_host_path
            )
            return

        os.makedirs(os.path.dirname(tmp_session_file), exist_ok=True)
        shutil.copy2(session_host_path, tmp_session_file)

        telegram_proxy = get_telegram_proxy(config)
        if telegram_proxy:
            logging.info("Telegram proxy enabled: %s:%s", telegram_proxy[1], telegram_proxy[2])

        last_connect_error = None
        for attempt in range(1, TELEGRAM_CONNECT_RETRIES + 1):
            # Пересоздаём клиента на каждой попытке: при сбое прокси-пул отдаёт
            # другой upstream-прокси. Иначе ретраи залипают на одном мёртвом прокси
            # (одиночные коннекты проходят, а застрявший клиент таймаутит весь прогон).
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            client = TelegramClient(
                tmp_session_name,
                config['api_id'],
                config['api_hash'],
                proxy=telegram_proxy,
            )
            try:
                for i in range(5):
                    try:
                        await client.connect()
                        last_connect_error = None
                        break
                    except sqlite3.OperationalError as e:
                        if 'database is locked' in str(e):
                            logging.warning("SQLite база заблокирована, пробуем снова через 3 секунды...")
                            await asyncio.sleep(3)
                        else:
                            raise
                else:
                    raise RuntimeError("Не удалось подключиться к Telegram из-за блокировки SQLite.")
                break
            except Exception as e:
                last_connect_error = e
                if attempt == TELEGRAM_CONNECT_RETRIES:
                    break
                delay = TELEGRAM_RETRY_DELAYS_SEC[min(attempt - 1, len(TELEGRAM_RETRY_DELAYS_SEC) - 1)] + random.uniform(0, 15)
                logging.warning(
                    "Не удалось подключиться к Telegram, попытка %s/%s: %s. Повтор через %.1f сек.",
                    attempt,
                    TELEGRAM_CONNECT_RETRIES,
                    e,
                    delay
                )
                await asyncio.sleep(delay)

        if last_connect_error is not None:
            logging.error("Не удалось подключиться к Telegram после %s попыток.", TELEGRAM_CONNECT_RETRIES)
            raise last_connect_error

        if not await client.is_user_authorized():
            logging.error(
                "Telegram-сессия найдена, но не авторизована. "
                "Запустите `docker exec telegram-to-sheets-app python -m telethon.sessions` и авторизуйтесь вручную."
            )
            return
        last_id = read_last_id()
        logging.info(f"Последний обработанный ID: {last_id}")
        messages = await client.get_messages(int(config['chat_id']), limit=500, min_id=last_id)
        new_messages = [m for m in messages if m.id > last_id]
        if not new_messages:
            logging.info("Новых сообщений нет.")
            return
        new_messages.sort(key=lambda m: m.id)
        rows_raw = []
        text_count = 0
        for m in new_messages:
            text = m.message.replace('\n', ' ') if m.message else ''
            if text.strip():
                text_count += 1
            rows_raw.append([m.id, m.date.strftime('%Y-%m-%d %H:%M:%S'), text])
        if text_count == 0:
            logging.warning("В новых сообщениях нет текстов.")
            return
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        client_gs = gspread.authorize(creds)
        google_creds = GoogleCreds.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        sheets_api = build('sheets', 'v4', credentials=google_creds, cache_discovery=False)
        spreadsheet = await retry_gspread(client_gs.open_by_key, config['google_sheet_id'])
        try:
            sheet_raw = await retry_gspread(spreadsheet.worksheet, 'Original data')
        except gspread.exceptions.WorksheetNotFound:
            sheet_raw = await retry_gspread(
                spreadsheet.add_worksheet,
                title='Original data',
                rows='1000',
                cols='10'
            )

        # ✅ Добавление заголовков, если их нет
        existing = await retry_gspread(sheet_raw.get_all_values)
        if not existing or not any(cell.strip() for cell in existing[0]):
            await retry_gspread(sheet_raw.insert_row, ['ID', 'Дата', 'Текст'], index=1)
        try:
            sheet_groups = await retry_gspread(spreadsheet.worksheet, 'Groups')
        except gspread.exceptions.WorksheetNotFound:
            sheet_groups = await retry_gspread(
                spreadsheet.add_worksheet,
                title='Groups',
                rows='100',
                cols='20'
            )

        # Гарантированно добавим заголовки, если пусто
        group_values = await retry_gspread(sheet_groups.get_all_values)
        if not group_values or not any(cell.strip() for cell in group_values[0]):
            await update_range(spreadsheet, 'Groups!A1:M1', [GROUPS_HEADER])

        category_rules = await load_category_rules(spreadsheet)

        # Добавляем новые сообщения в первую вкладку
        await retry_gspread(sheet_raw.append_rows, rows_raw)
        save_last_id(new_messages[-1].id)
        logging.info(f"Добавлено сообщений: {len(new_messages)} | Текстовых: {text_count}")

        # Читаем ВСЕ логи с первой вкладки для анализа
        # Удаляем строки из Original data старше 30 дней
        raw_rows = await retry_gspread(sheet_raw.get_all_values)
        header = raw_rows[0]
        rows_to_keep = [header]
        cutoff = datetime.now() - timedelta(days=30)

        for row in raw_rows[1:]:
            if len(row) < 2:
                continue
            try:
                try:
                    log_date = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        log_date = datetime.fromisoformat(row[1])
                    except Exception:
                        continue
                if log_date > cutoff:
                    rows_to_keep.append(row)
            except Exception:
                rows_to_keep.append(row)  # если дата кривая — не удаляем

        # Полностью перезаписываем таблицу только нужными строками
        await retry_gspread(sheet_raw.clear)
        await retry_gspread(sheet_raw.append_rows, rows_to_keep)
        logs_data = []
        for row in rows_to_keep[1:]:  # пропускаем заголовок
            if len(row) < 3:
                continue
            try:
                log_id = int(row[0])
                try:
                    log_date = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        log_date = datetime.fromisoformat(row[1])
                    except Exception:
                        continue
                log_text = row[2]
                logs_data.append({'id': log_id, 'date': log_date, 'text': log_text})
            except Exception as e:
                logging.warning(f"Ошибка при разборе строки: {row} | {e}")

        # Анализируем все логи (со склейкой цепочек внутри count_and_aggregate)
        error_data = count_and_aggregate(logs_data)

        # Пересобираем вкладку Groups целиком:
        # - счётчики свежих групп из error_data;
        # - группы, не встреченные в 30-дневном окне, получают нулевые счётчики
        #   (раньше хранили застывшие числа — это враньё в данных);
        # - ручные колонки (Статус/Вердикт/Срочность/Причина/Действие/Оценено)
        #   сохраняются как есть;
        # - группы без появлений дольше RETENTION_DAYS уезжают в Archive.
        group_rows_all = await retry_gspread(sheet_groups.get_all_values)
        # Колонки читаем ПО ИМЕНАМ: порядок колонок менялся (ID переехал в начало),
        # позиционное чтение затирало бы ручные вердикты при переходе.
        old_header = group_rows_all[0] if group_rows_all else []
        col_idx = {name.strip(): i for i, name in enumerate(old_header) if name.strip()}

        def old_col(row, name):
            i = col_idx.get(name)
            return row[i].strip() if i is not None and len(row) > i else ''

        existing_groups = {}  # шаблон -> сохранённые ручные колонки
        for row in group_rows_all[1:]:
            pattern = old_col(row, 'Ошибка (шаблон)')
            if not pattern:
                continue
            existing_groups[pattern] = {
                'category': old_col(row, 'Категория'),
                'last_seen': old_col(row, 'Последнее появление'),
                'status': old_col(row, 'Статус'),
                'verdict': old_col(row, 'Вердикт'),
                'urgency': old_col(row, 'Срочность'),
                'cause': old_col(row, 'Причина'),
                'action': old_col(row, 'Действие'),
                'assessed': old_col(row, 'Оценено'),
            }

        now_utc = datetime.now(timezone.utc)
        retention_cutoff = (now_utc - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')

        final_rows = []
        archive_rows = []
        seen_patterns = set()

        def build_row(pattern, counts, last_seen, saved):
            category = (saved or {}).get('category', '').strip() or extract_category(pattern, category_rules)
            status = (saved or {}).get('status', '').strip()
            verdict = (saved or {}).get('verdict', '')
            if not status:
                status = 'не обработано' if not verdict.strip() else 'обработано'
            s = saved or {}
            return [
                group_id(pattern), category, pattern,
                str(counts['1d']), str(counts['7d']), str(counts['30d']), last_seen,
                status, verdict, s.get('urgency', ''),
                s.get('cause', ''), s.get('action', ''), s.get('assessed', ''),
            ]

        # Порядок строк: сортировка по «За 1 день» (по требованию владельца).
        # Ориентир в дайджестах — колонка ID (первая), а не номер строки.
        zero = {'1d': 0, '7d': 0, '30d': 0}
        for pattern, saved in existing_groups.items():
            data = error_data.get(pattern)
            if data is not None:
                last_seen_str = data['last_seen'].strftime('%Y-%m-%d %H:%M:%S') if data['last_seen'] else ''
                final_rows.append(build_row(
                    pattern, data['counts'],
                    max(last_seen_str, saved.get('last_seen', '')), saved
                ))
                seen_patterns.add(pattern)
                continue
            # Не встречалась в окне: счётчики в ноль; совсем протухшая — в архив
            row = build_row(pattern, zero, saved.get('last_seen', ''), saved)
            if saved.get('last_seen', '') and saved['last_seen'] < retention_cutoff:
                archive_rows.append(row)
            else:
                final_rows.append(row)

        # Новые группы — вниз, между собой по объёму за 30 дней
        for pattern, data in sorted(error_data.items(), key=lambda x: x[1]['counts']['30d'], reverse=True):
            if pattern in seen_patterns or pattern in existing_groups:
                continue
            last_seen_str = data['last_seen'].strftime('%Y-%m-%d %H:%M:%S') if data['last_seen'] else ''
            final_rows.append(build_row(pattern, data['counts'], last_seen_str, None))
            seen_patterns.add(pattern)

        # Архив: дозаписываем протухшие группы (вердикты сохраняются в истории)
        if archive_rows:
            try:
                sheet_archive = await retry_gspread(spreadsheet.worksheet, ARCHIVE_SHEET_TITLE)
            except gspread.exceptions.WorksheetNotFound:
                sheet_archive = await retry_gspread(
                    spreadsheet.add_worksheet, title=ARCHIVE_SHEET_TITLE, rows='1000', cols='20')
                await update_range(spreadsheet, f'{ARCHIVE_SHEET_TITLE}!A1:M1', [GROUPS_HEADER])
            await retry_gspread(sheet_archive.append_rows, archive_rows)
            logging.info(f"В архив перенесено групп: {len(archive_rows)}")

        # Сортировка по «За 1 день», затем по «За 30 дней»
        d1_i = GROUPS_HEADER.index('За 1 день')
        d30_i = GROUPS_HEADER.index('За 30 дней')
        final_rows.sort(key=lambda r: (int(r[d1_i] or 0), int(r[d30_i] or 0)), reverse=True)

        # Полная перезапись Groups (архив уже сохранён, потеря невозможна)
        await retry_gspread(sheet_groups.clear)
        await retry_gspread(sheet_groups.append_rows, [GROUPS_HEADER] + final_rows)
        logging.info(f"Groups перезаписан: {len(final_rows)} групп (новых: "
                     f"{len(seen_patterns - set(existing_groups))})")

    except Exception as e:
        logging.error(f"Ошибка в main: {e}", exc_info=True)
    finally:
        # Отдельная пересортировка не нужна: Groups перезаписывается
        # уже отсортированным по "За 30 дней" в основном блоке.
        if client is not None:
            await client.disconnect()
        if tmp_session_file and session_host_path and os.path.exists(tmp_session_file):
            try:
                shutil.copy2(tmp_session_file, session_host_path)
            except Exception as copy_err:
                logging.error(f"Не удалось сохранить Telegram-сессию: {copy_err}")
        logging.info("Скрипт успешно завершил работу.")

if __name__ == '__main__':
    asyncio.run(main())
