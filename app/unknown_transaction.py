import hashlib
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

groups = defaultdict(lambda: {'1d': 0, '30d': 0, 'amount': 0.0,
                              'first': None, 'last': None})
ids_for_dash_group = []

# ===== История неучтённых операций (вкладка Unknown tx history) =====
# Зачем: суточная вкладка Unknown tx перезаписывается каждый запуск, и типы,
# появившиеся в выходные, к понедельнику видны только нулями, а через 30 дней
# (когда сырые логи уедут из Original data) исчезают вовсе. История хранит
# каждый тип ОДИН раз и удаляет строку через 30 дней после ПЕРВОГО появления.
HISTORY_SHEET = 'Unknown tx history'
HISTORY_HEADER = [
    'ID', 'Платформа', 'operation_type_name', 'supplier_oper_name',
    'doc_type_name', 'bonus_type_name',
    'Первый раз', 'Последний раз', 'Всего логов', 'Сумма ₽', 'Комментарий',
]
HISTORY_RETENTION_DAYS = 30


def history_id(key) -> str:
    return hashlib.sha1('|'.join(key).encode('utf-8')).hexdigest()[:8]


def update_history(spreadsheet, groups_data):
    """Upsert типов в историю: новые добавляются, у известных обновляются
    «Последний раз», счётчик и сумма. Комментарий (ручная колонка) не трогаем.
    Строки старше HISTORY_RETENTION_DAYS от первого появления удаляются."""
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        sheet = spreadsheet.worksheet(HISTORY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=HISTORY_SHEET, rows='500', cols='12')
        sheet.append_row(HISTORY_HEADER)

    existing_rows = sheet.get_all_values()
    header = existing_rows[0] if existing_rows else HISTORY_HEADER
    idx = {name.strip(): i for i, name in enumerate(header) if name.strip()}

    def cell(row, name, default=''):
        i = idx.get(name)
        return row[i].strip() if i is not None and len(row) > i else default

    known = {}
    for row in existing_rows[1:]:
        rid = cell(row, 'ID')
        if rid:
            known[rid] = row

    cutoff = (datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)).strftime('%Y-%m-%d')
    out, seen, added = [], set(), 0

    for key, stats in groups_data.items():
        if key[0] == 'Некорректный лог':
            continue  # это не тип операции, а битый лог
        rid = history_id(key)
        seen.add(rid)
        prev = known.get(rid)
        first = cell(prev, 'Первый раз', today) if prev else today
        # Счётчик копим по суткам; повторный запуск в тот же день не удваивает
        prev_total = 0
        prev_amount = 0.0
        if prev:
            try:
                prev_total = int(cell(prev, 'Всего логов') or 0)
                prev_amount = float((cell(prev, 'Сумма ₽') or '0').replace(' ', '').replace(',', '.'))
            except ValueError:
                pass
            already_today = cell(prev, 'Последний раз') == today
        else:
            already_today = False
            added += 1
        total = prev_total if already_today else prev_total + stats['1d']
        amount = prev_amount if already_today else prev_amount + stats['amount']
        last = today if stats['1d'] else cell(prev, 'Последний раз', '') if prev else ''
        if first < cutoff:
            continue  # протух — не переносим
        out.append([
            rid, key[0], key[2], key[3], key[1], key[5],
            first, last or first, str(total), f'{amount:.2f}',
            cell(prev, 'Комментарий') if prev else '',
        ])

    # Типы, которых сегодня не было в окне, но которые ещё не протухли
    for rid, row in known.items():
        if rid in seen:
            continue
        if cell(row, 'Первый раз') and cell(row, 'Первый раз') >= cutoff:
            out.append([cell(row, name) for name in HISTORY_HEADER])

    out.sort(key=lambda r: (r[7], r[6]), reverse=True)  # свежие сверху
    sheet.clear()
    sheet.append_rows([HISTORY_HEADER] + out)
    logging.info('История неучтённых операций: %s типов (новых: %s)', len(out), added)
    print(f"История неучтённых операций обновлена: {len(out)} типов (новых: {added}).")

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
        amount_raw = extract_from_json('amount', text)
        try:
            groups[key]['amount'] += float(str(amount_raw).replace(',', '.'))
        except (TypeError, ValueError):
            pass

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

# История неучтённых операций (отдельная вкладка, живёт 30 дней от первого появления)
try:
    update_history(spreadsheet, groups)
except Exception as exc:
    logging.error('Не удалось обновить историю неучтённых операций: %s', exc, exc_info=True)
    print(f"Ошибка обновления истории: {exc}")
