import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import re
import random
import functools
import sqlite3
import gspread
from googleapiclient.errors import HttpError

from telethon import TelegramClient
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as GoogleCreds

# Константы
BASE_DIR = '/app'
LOG_PATH = os.path.join(BASE_DIR, 'logs/telegram_to_sheets.log')
LAST_ID_FILE = os.path.join(BASE_DIR, 'last_message_id.txt')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')

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


SPECIAL_PATTERNS = [
    "Account updating status was cleaned",
    "Syncing for more than",
    "Contentanalytics api key not working",
    "Advert api key not working",
    "Subscription turnover is higher than calculated for user",
    "currentDate",
    "puppet service is inactive",
    "Load average is too high",
    "Recurrent payment failed",
    "SQLSTATE",
    "ServiceTransactionReportJob failed",
    "cURL error",
    "Unknown transaction type",
    "paymentFailed",
    "DEBUG",
    "Syncing ozon transactions for account",
    "Ozon API response error for account",
    "Orders integrity fail",
    "Failed to download image",
    "Partner not found",
    "Account is blocked",
    "The given data was invalid",
    "Tinkoff payment error",
    "Subscription changed for user",
    "Disk space is critically low"
]

def should_normalize(text: str) -> bool:
    return any(pattern in text for pattern in SPECIAL_PATTERNS)

def extract_category(error_pattern: str) -> str:
    if "timed out" in error_pattern:
        return "TimedOut"
    if "Введен не валидный API ключ" in error_pattern:
        return "WrongApi"
    if "data was invalid" in error_pattern:
        return "InvalidData"
    if "on null" in error_pattern:
        return "NullProperty"
    if "api key not working" in error_pattern:
        return "ApiKeyNotWorking"
    if "ClientID" in error_pattern:
        return "InvalidClientID"
    if any(pat in error_pattern for pat in ["paymentFailed", "payment failed", "payment error"]):
        return "PaymentFailed"
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

def normalize_error_pattern(text: str) -> str:
    if not text:
        return ''
    
    original_text = text

    if should_normalize(text):
        # Заменяем части URL: volXXXX и partXXXXX
        text = re.sub(r'vol\d+', 'vol<num>', text)
        text = re.sub(r'part\d+', 'part<num>', text)
        
        # Можно объединить в одну строку, если хочешь:
        # text = re.sub(r'\b[a-zA-Z]+\d+\b', '<num>', text)

        # Удаляем JSON-объекты
        text = re.sub(r'\{.*?\}', '{}', text)

        # Заменяем email
        text = re.sub(r'[\w\.-]+@[\w\.-]+', '<email>', text)

        # Заменяем дату и время
        text = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', '<datetime>', text)

        # Заменяем хеши (SHA1/SHA256 и т.п.)
        text = re.sub(r'\b[a-f0-9]{32,64}\b', '<hash>', text)

        # Заменяем дробные числа
        text = re.sub(r'\b\d+\.\d+\b', '<float>', text)

        # Заменяем большие числа
        text = re.sub(r'\b\d{4,}\b', '<num>', text)

        # Заменяем оставшиеся числа
        text = re.sub(r'\b\d+\b', '<num>', text)

        return text.strip()
    else:
        return original_text.strip()


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

async def retry_gspread(func, *args, retries=5, delay=3, backoff=2, **kwargs):
    for attempt in range(retries):
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        except gspread.exceptions.APIError as e:
            if '503' in str(e) or '502' in str(e) or 'temporarily_unavailable' in str(e):
                await asyncio.sleep(delay + random.uniform(0, 2))
                delay *= backoff
            else:
                raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay + random.uniform(0, 2))
            delay *= backoff

async def retry_google_api(api_call, retries=5, delay=3, backoff=2):
    for attempt in range(retries):
        try:
            return api_call()
        except HttpError as e:
            if e.resp.status in [502, 503, 504]:
                await asyncio.sleep(delay + random.uniform(0, 2))
                delay *= backoff
            else:
                raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay + random.uniform(0, 2))
            delay *= backoff


def count_and_aggregate(logs):
    now = datetime.now(timezone.utc)
    error_data = defaultdict(lambda: {
        'addresses': set(),
        'counts': {'1d': 0, '7d': 0, '30d': 0},
        'last_seen': None
    })
    for log in logs:
        raw_text, address = extract_error_and_address(log['text'])
        error_pattern = normalize_error_pattern(raw_text)
        if not error_pattern:
            continue
        data = error_data[error_pattern]
        data['addresses'].add(address)
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
    try:
        os.chdir(BASE_DIR)
        clean_old_logs(LOG_PATH, days=7)
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        client = TelegramClient(config['session_name'], config['api_id'], config['api_hash'])
        for i in range(5):
            try:
                await client.start()
                break
            except sqlite3.OperationalError as e:
                if 'database is locked' in str(e):
                    logging.warning("SQLite база заблокирована, пробуем снова через 3 секунды...")
                    await asyncio.sleep(3)
                else:
                    raise
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
        spreadsheet = client_gs.open_by_key(config['google_sheet_id'])
        try:
            sheet_raw = spreadsheet.worksheet('Original data')
        except gspread.exceptions.WorksheetNotFound:
            sheet_raw = spreadsheet.add_worksheet(title='Original data', rows='1000', cols='10')

        # ✅ Добавление заголовков, если их нет
        existing = await retry_gspread(sheet_raw.get_all_values)
        if not existing or not any(cell.strip() for cell in existing[0]):
            sheet_raw.insert_row(['ID', 'Дата', 'Текст'], index=1)
        try:
            sheet_groups = spreadsheet.worksheet('Groups')
        except gspread.exceptions.WorksheetNotFound:
            sheet_groups = spreadsheet.add_worksheet(title='Groups', rows='100', cols='20')

        # Гарантированно добавим заголовки, если пусто
        group_values = await retry_gspread(sheet_groups.get_all_values)
        if not group_values or not any(cell.strip() for cell in group_values[0]):
            sheet_groups.update(
                values=[[
                    "Категория", "Ошибка (шаблон)", "Адреса", "Код из Bitbucket", "GPT-ответ",
                    "За 1 день", "За 7 дней", "За 30 дней", "Последнее появление", "Статус"
                ]],
                range_name='A1:J1'
            )

        # Добавляем новые сообщения в первую вкладку
        await retry_gspread(sheet_raw.append_rows, rows_raw)
        save_last_id(new_messages[-1].id)
        logging.info(f"Добавлено сообщений: {len(new_messages)} | Текстовых: {text_count}")

        # Читаем ВСЕ логи с первой вкладки для анализа
        # Удаляем строки из Original data старше 30 дней
        raw_rows = sheet_raw.get_all_values()
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

        # Анализируем все логи
        error_data = count_and_aggregate(logs_data)

        # Формируем таблицу групп без столбца "Кол-во уникальных адресов"
        group_rows = [["Ошибка (шаблон)", "Адреса", "За 1 день", "За 7 дней", "За 30 дней", "Последнее появление"]]
        for error_pattern, data in sorted(error_data.items(), key=lambda x: x[1]['counts']['30d'], reverse=True):
            addresses_str = ', '.join(sorted(data['addresses']))
            last_seen_str = data['last_seen'].strftime('%Y-%m-%d %H:%M:%S') if data['last_seen'] else ''
            group_rows.append([
                error_pattern,
                addresses_str,
                data['counts']['1d'],
                data['counts']['7d'],
                data['counts']['30d'],
                last_seen_str
            ])

        # ... (всё до # Анализируем все логи без изменений)


        # Читаем текущие строки из листа Groups
        group_rows_all = await retry_gspread(sheet_groups.get_all_values)
        # Проверяем, есть ли заголовки (первый элемент непустой)
        if not group_rows_all or not any(group_rows_all[0]):
            sheet_groups.update(
                values=[[
                    "Категория",
                    "Ошибка (шаблон)",
                    "Адреса",
                    "Код из Bitbucket",
                    "GPT-ответ",
                    "За 1 день",
                    "За 7 дней",
                    "За 30 дней",
                    "Последнее появление",
                    "Статус"
                ]],
                range_name='A1:J1'
            )

        # ВСЕГДА получаем свежие данные после добавления заголовков
        group_rows_all = await retry_gspread(sheet_groups.get_all_values)

        # Удаление строк, у которых за 30 дней = 0
        rows_to_delete = []
        for idx, row in enumerate(group_rows_all[1:], start=2):  # начиная со 2 строки
            try:
                count_30d = int(row[7]) if len(row) > 7 and row[7].strip().isdigit() else 0
                if count_30d == 0:
                    rows_to_delete.append(idx)
            except Exception:
                continue

        # Удаляем строки в обратном порядке, чтобы индексы не сдвигались
        for idx in reversed(rows_to_delete):
            await retry_gspread(sheet_groups.delete_rows, idx)

        header = group_rows_all[0]
        existing_groups = {}  # error_pattern -> (row_idx, row_data)
        for i, row in enumerate(group_rows_all[1:], start=2):
            if len(row) < 2:
                continue
            error_pattern_key = row[1].strip()
            existing_groups[error_pattern_key] = (i, row)

        # Подготовка к пакетному обновлению с разбиением на пачки по 100
        requests = []
        new_group_rows = []
        for error_pattern, data in sorted(error_data.items(), key=lambda x: x[1]['counts']['30d'], reverse=True):
            addresses_str = ', '.join(sorted(data['addresses']))
            last_seen_str = data['last_seen'].strftime('%Y-%m-%d %H:%M:%S') if data['last_seen'] else ''

            category = extract_category(error_pattern)
            new_row = [
                category,
                error_pattern,
                addresses_str,
                '',  # Код
                '',  # GPT
                str(data['counts']['1d']),
                str(data['counts']['7d']),
                str(data['counts']['30d']),
                last_seen_str,
                'не обработано' if not should_normalize(error_pattern) and addresses_str.strip() else ''
            ]

            if error_pattern in existing_groups:
                row_idx, existing_row = existing_groups[error_pattern]

                # Сохраняем старые значения, чтобы не затирать их
                existing_code = existing_row[3] if len(existing_row) > 3 else ''
                existing_gpt = existing_row[4] if len(existing_row) > 4 else ''
                existing_status = existing_row[9] if len(existing_row) > 9 else ''

                category = extract_category(error_pattern)
                updated_row = [
                    category,
                    error_pattern,
                    addresses_str,
                    existing_code,
                    existing_gpt,
                    str(data['counts']['1d']),
                    str(data['counts']['7d']),
                    str(data['counts']['30d']),
                    last_seen_str,
                    existing_status if existing_status.strip() else (
                        'не обработано' if not should_normalize(error_pattern) and addresses_str.strip() else ''
                    )
                ]

                cell_range = f'Groups!A{row_idx}:J{row_idx}'  # <- добавили имя листа
                requests.append({
                    'range': cell_range,
                    'values': [updated_row]
                })
            else:
                if error_pattern not in existing_groups:
                    new_group_rows.append(new_row)

        if new_group_rows:
            await retry_gspread(sheet_groups.append_rows, new_group_rows)

        # Отправка batch-запросов пачками по 100
        if requests:
            data = [{'range': req['range'], 'values': req['values'], 'majorDimension': 'ROWS'} for req in requests]
            await retry_google_api(sheets_api.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet.id,
                body={
                    'valueInputOption': 'USER_ENTERED',
                    'data': data
                }
            ).execute)

# ... (finally и остальное без изменений)

    except Exception as e:
        logging.error(f"Ошибка в main: {e}", exc_info=True)
    finally:
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
            client_gs = gspread.authorize(creds)
            spreadsheet = client_gs.open_by_key(config['google_sheet_id'])
            sheet_groups = spreadsheet.worksheet('Groups')

            # Получаем все строки
            all_rows = sheet_groups.get_all_values()
            if not all_rows:
                logging.info("Вкладка Groups пуста, сортировка не требуется.")
            else:
                header = all_rows[0]
                body = all_rows[1:] if len(all_rows) > 1 else []

                def parse_row(row):
                    try:
                        return int(row[5]) if len(row) > 5 and row[5].strip().isdigit() else 0
                    except:
                        return 0

                body.sort(key=parse_row, reverse=True)
                new_data = [header] + body

                await retry_gspread(sheet_groups.clear)
                await retry_gspread(sheet_groups.append_rows, new_data)
                logging.info("Вкладка 'Groups' отсортирована по столбцу 'За 1 день' (через Python).")

        except Exception as sort_error:
            logging.error(f"Ошибка при сортировке вкладки Groups: {sort_error}")

        await client.disconnect()
        logging.info("Скрипт успешно завершил работу.")

if __name__ == '__main__':
    asyncio.run(main())
