"""
Миграция вкладки Groups на новую нормализацию (normalize.py).

Собирает перегруппированные данные в ОТДЕЛЬНУЮ вкладку Groups_new:

1. Группы за последние 30 дней пересобираются ИЗ СЫРЫХ данных (Original data)
   со склейкой цепочек разрезанных сообщений (merge_fragment_chains) —
   ровно так же будет считать новый коллектор. Это восстанавливает целые
   логи, которые раньше были растащены на куски.
2. Старые шаблоны из Groups, не встречавшиеся за 30 дней (сырья уже нет),
   переносятся ре-нормализацией с их застывшими счётчиками.
   Легаси-обрезки (фрагменты без головы) при этом пропускаются — их головы
   уже есть отдельными строками, хвосты лишь дублировали их.

Колонки "Код из Bitbucket", "GPT-ответ", "Адреса" упразднены.
Добавлены колонки вердикта. Статус всех групп — "не обработано".

Боевая вкладка Groups НЕ изменяется. Подмена — отдельным шагом после сверки.

Запуск: python3 migrate_groups.py
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from normalize import is_fragment, merge_fragment_chains, normalize_error_pattern

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NEW_SHEET = 'Groups_new'

NEW_HEADER = [
    "Категория", "Ошибка (шаблон)",
    "За 1 день", "За 7 дней", "За 30 дней", "Последнее появление",
    "Статус", "Вердикт", "Срочность", "Причина", "Действие", "Оценено",
]


def parse_date(value: str):
    for fmt in ('%Y-%m-%d %H:%M:%S',):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main() -> None:
    with open(os.path.join(BASE_DIR, 'config.json')) as f:
        config = json.load(f)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        os.path.join(BASE_DIR, 'google-credentials.json'), scope)
    spreadsheet = gspread.authorize(creds).open_by_key(config['google_sheet_id'])

    # ---- 1. Свежие группы из сырых логов (со склейкой цепочек) ----
    raw_rows = spreadsheet.worksheet('Original data').get_all_values()
    logs = []
    for r in raw_rows[1:]:
        if len(r) >= 3 and r[0].strip().isdigit():
            d = parse_date(r[1].strip())
            if d:
                logs.append({'id': int(r[0]), 'date': d, 'text': r[2]})

    merged = merge_fragment_chains(logs)
    now = datetime.now()

    groups = defaultdict(lambda: {
        'category': '', 'd1': 0, 'd7': 0, 'd30': 0, 'last_seen': '',
    })
    for log in merged:
        pattern = normalize_error_pattern(log['text'])
        if not pattern:
            continue
        g = groups[pattern]
        delta = now - log['date']
        if delta <= timedelta(days=1):
            g['d1'] += 1
        if delta <= timedelta(days=7):
            g['d7'] += 1
        if delta <= timedelta(days=30):
            g['d30'] += 1
        seen = log['date'].strftime('%Y-%m-%d %H:%M:%S')
        if seen > g['last_seen']:
            g['last_seen'] = seen

    fresh_count = len(groups)

    # ---- 2. Легаси: шаблоны старше 30 дней из текущей Groups ----
    old_rows = spreadsheet.worksheet('Groups').get_all_values()
    header = old_rows[0]
    idx = {name: header.index(name) for name in header if name}

    def col(row, name, default=''):
        i = idx.get(name)
        return row[i] if i is not None and len(row) > i else default

    def as_int(v):
        try:
            return int(str(v).strip() or 0)
        except ValueError:
            return 0

    legacy_added = 0
    legacy_fragments_skipped = 0
    cutoff = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    for row in old_rows[1:]:
        pattern_raw = col(row, 'Ошибка (шаблон)').strip()
        if not pattern_raw:
            continue
        last = col(row, 'Последнее появление').strip()
        if last >= cutoff:
            continue  # свежее уже пересчитано из сырья
        if is_fragment(pattern_raw):
            legacy_fragments_skipped += 1
            continue
        pattern = normalize_error_pattern(pattern_raw)
        if not pattern or pattern in groups:
            continue
        g = groups[pattern]
        legacy_added += 1
        if not g['category']:
            g['category'] = col(row, 'Категория').strip()
        g['d30'] += as_int(col(row, 'За 30 дней'))
        if last > g['last_seen']:
            g['last_seen'] = last

    # ---- 3. Запись ----
    out_rows = [NEW_HEADER]
    for pattern, g in sorted(groups.items(), key=lambda kv: kv[1]['d30'], reverse=True):
        out_rows.append([
            g['category'], pattern[:2000],
            str(g['d1']), str(g['d7']), str(g['d30']), g['last_seen'],
            'не обработано', '', '', '', '', '',
        ])

    try:
        ws = spreadsheet.worksheet(NEW_SHEET)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=NEW_SHEET, rows=str(len(out_rows) + 50), cols='20')

    ws.update(values=out_rows, range_name='A1')

    print(f'{datetime.now():%H:%M:%S} | сырых логов: {len(logs)}, после склейки цепочек: {len(merged)}')
    print(f'свежих групп (30 дней, из сырья): {fresh_count}')
    print(f'легаси-групп (старше 30 дней): +{legacy_added} '
          f'(обрезков пропущено: {legacy_fragments_skipped})')
    print(f'итого в {NEW_SHEET}: {len(out_rows) - 1}')


if __name__ == '__main__':
    main()
