import hashlib
import json
import os
import logging
from datetime import datetime, timedelta

from unknown_tx_stats import aggregate_unknown_transactions, extract_from_json

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
data = rows[1:]

# ===== История неучтённых операций (вкладка Unknown tx history) =====
# Зачем: суточная вкладка Unknown tx перезаписывается каждый запуск, и типы,
# появившиеся в выходные, к понедельнику видны только нулями, а через 30 дней
# (когда сырые логи уедут из Original data) исчезают вовсе. История хранит
# каждый тип ОДИН раз и удаляет строку через 30 дней после ПЕРВОГО появления.
HISTORY_SHEET = 'Unknown tx history'
HISTORY_HEADER = [
    'ID', 'Платформа', 'operation_type', 'operation_type_name',
    'supplier_oper_name', 'doc_type_name', 'bonus_type_name',
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
            rid, key[0], key[1], key[3], key[4], key[2], key[6],
            first, last or first, str(total), f'{amount:.2f}',
            cell(prev, 'Комментарий') if prev else '',
        ])

    # Типы, которых сегодня не было в окне, но которые ещё не протухли
    for rid, row in known.items():
        if rid in seen:
            continue
        if cell(row, 'Первый раз') and cell(row, 'Первый раз') >= cutoff:
            out.append([cell(row, name) for name in HISTORY_HEADER])

    out.sort(key=lambda r: (r[8], r[7]), reverse=True)  # свежие сверху
    sheet.clear()
    sheet.append_rows([HISTORY_HEADER] + out)
    logging.info('История неучтённых операций: %s типов (новых: %s)', len(out), added)
    print(f"История неучтённых операций обновлена: {len(out)} типов (новых: {added}).")

# Подготовка листа (очистка до начала обработки)
try:
    sheet_tx = spreadsheet.worksheet('Unknown tx')
except gspread.exceptions.WorksheetNotFound:
    sheet_tx = spreadsheet.add_worksheet(title='Unknown tx', rows='100', cols='12')
else:
    sheet_tx.clear()

sheet_tx.append_row([
    'Платформа (ВБ/ОЗОН/Некорректный лог)',
    'operation_type',
    'doc_type_name',
    'operation_type_name',
    'supplier_oper_name',
    'payment_processing',
    'bonus_type_name',
    'За 1 день',
    'ID некорректных логов из Original data'
])

groups, ids_for_dash_group = aggregate_unknown_transactions(data)

# Сортировка по платформе вручную
platform_priority = {'ВБ': 0, 'ОЗОН': 1, 'Некорректный лог': 2}
sorted_keys = sorted(groups.items(), key=lambda x: (platform_priority.get(x[0][0], 99), -x[1]['1d']))

# Запись строк
for key, stats in sorted_keys:
    base = list(key)
    if key[0] == 'Некорректный лог':
        row = base + [stats['1d'], ', '.join(ids_for_dash_group)]
    else:
        row = base + [stats['1d'], '']
    sheet_tx.append_row(row)

print("Сводка по 'Unknown transaction type' успешно записана.")

# История неучтённых операций (отдельная вкладка, живёт 30 дней от первого появления)
try:
    update_history(spreadsheet, groups)
except Exception as exc:
    logging.error('Не удалось обновить историю неучтённых операций: %s', exc, exc_info=True)
    print(f"Ошибка обновления истории: {exc}")
