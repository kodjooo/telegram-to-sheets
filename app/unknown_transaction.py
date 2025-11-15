import json
import re
import os
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Настройка логирования
logging.basicConfig(
    filename='/app/logs/unknown_tx.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)

# Базовая директория приложения
script_dir = '/app'

# Загрузка конфигурации
with open(os.path.join(script_dir, 'config.json'), 'r') as f:
    config = json.load(f)

GOOGLE_SHEET_ID = config['google_sheet_id']

# Авторизация
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds_path = os.path.join(script_dir, 'google-credentials.json')
creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
client = gspread.authorize(creds)

# Доступ к таблице
spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

# Лист с оригинальными логами
sheet = spreadsheet.worksheet('Original data')
rows = sheet.get_all_values()
header = rows[0]
data = rows[1:]
id_col_index = 0

cutoff_1d = datetime.now() - timedelta(days=1)
cutoff_30d = datetime.now() - timedelta(days=30)

def extract_from_json(field, text):
    try:
        start = text.find('{')
        if start == -1:
            return ''
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            last_close = text.rfind('}')
            if last_close == -1 or last_close < start:
                return ''
            json_str = text[start:last_close+1]
        else:
            json_str = text[start:end]
        try:
            obj = json.loads(json_str)
        except json.JSONDecodeError:
            json_str_fixed = json_str.replace('\\', '\\\\')
            obj = json.loads(json_str_fixed)
        value = obj.get(field, '')
        if value in ('', None):
            pattern = rf'"{field}"\s*:\s*"([^"]*)"'
            match = re.search(pattern, text)
            return match.group(1) if match else ''
        return str(value)
    except Exception:
        pattern = rf'"{field}"\s*:\s*"([^"]*)"'
        match = re.search(pattern, text)
        return match.group(1) if match else ''

groups = defaultdict(lambda: {'1d': 0, '30d': 0})
ids_for_dash_group = []

# Подготовка листа (очистка до начала обработки)
try:
    sheet_tx = spreadsheet.worksheet('Unknown tx')
except gspread.exceptions.WorksheetNotFound:
    sheet_tx = spreadsheet.add_worksheet(title='Unknown tx', rows='100', cols='10')
else:
    sheet_tx.clear()

sheet_tx.append_row([
    'Платформа (ВБ/ОЗОН/Некорректный лог)',
    'doc_type_name',
    'operation_type_name',
    'supplier_oper_name',
    'payment_processing',
    'bonus_type_name',
    'За 1 день',
    'ID некорректных логов из Original data'
])

for row in data:
    if len(row) < 3:
        continue
    try:
        log_time = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
    except Exception:
        continue

    text = row[2]
    if 'Unknown transaction type' not in text:
        continue

    supplier = extract_from_json('supplier_oper_name', text)
    doc_type = extract_from_json('doc_type_name', text)
    operation_type = extract_from_json('operation_type', text)
    operation_type_name = extract_from_json('operation_type_name', text)
    payment_processing = extract_from_json('payment_processing', text)
    bonus_type = extract_from_json('bonus_type_name', text)

    if doc_type.startswith('/'):
        doc_type = doc_type[1:]

    platform = 'ВБ' if supplier else 'ОЗОН' if operation_type else '—'

    if platform == '—' and not doc_type and not operation_type and not payment_processing and not bonus_type:
        platform = 'Некорректный лог'

    key = (
        platform,
        doc_type or '—',
        operation_type_name or '—',
        supplier or '—',
        payment_processing or '—',
        bonus_type or '—'
    )

    if platform == 'Некорректный лог':
        logging.debug('Лог попал в категорию некорректных:')
        logging.debug(f'→ supplier: {supplier}')
        logging.debug(f'→ doc_type: {doc_type}')
        logging.debug(f'→ operation_type: {operation_type}')
        logging.debug(f'→ payment_processing: {payment_processing}')
        logging.debug(f'→ bonus_type: {bonus_type}')
        logging.debug(f'→ log text: {text[:500]}')

    if log_time >= cutoff_1d:
        groups[key]['1d'] += 1

    if platform == 'Некорректный лог':
        ids_for_dash_group.append(row[id_col_index])

# Сортировка по платформе вручную
platform_priority = {'ВБ': 0, 'ОЗОН': 1, 'Некорректный лог': 2}
sorted_keys = sorted(groups.items(), key=lambda x: (platform_priority.get(x[0][0], 99), -x[1]['1d']))

# Запись строк
for key, stats in sorted_keys:
    if key[0] == 'Некорректный лог':
        row = list(key) + [stats['1d'], ', '.join(ids_for_dash_group)]
    else:
        row = list(key) + [stats['1d'], '']
    sheet_tx.append_row(row)

print("Сводка по 'Unknown transaction type' успешно записана.")
