"""
Dry-run перегруппировки Groups по новой нормализации (normalize.py).

НИЧЕГО не меняет в таблице. Читает вкладку Groups, прогоняет шаблоны через
новую нормализацию и пишет отчёт: сколько групп было/станет, самые крупные
склейки с примерами — для ручного ревью правил.

Запуск локально: python3 app/dry_run_regroup.py [путь_к_отчёту.md]
"""

import json
import os
import sys
from collections import defaultdict

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from normalize import normalize_error_pattern

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')


def main() -> None:
    report_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, '..', 'regroup_report.md')

    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(config['google_sheet_id']).worksheet('Groups')

    rows = sheet.get_all_values()
    header = rows[0]
    pattern_idx = header.index('Ошибка (шаблон)')
    try:
        d30_idx = header.index('За 30 дней')
    except ValueError:
        d30_idx = None

    old_patterns = []
    counts_30d = {}
    for row in rows[1:]:
        if len(row) <= pattern_idx:
            continue
        p = row[pattern_idx].strip()
        if not p:
            continue
        old_patterns.append(p)
        if d30_idx is not None and len(row) > d30_idx:
            try:
                counts_30d[p] = counts_30d.get(p, 0) + int(row[d30_idx] or 0)
            except ValueError:
                pass

    groups = defaultdict(list)  # новый шаблон -> [старые шаблоны]
    for p in old_patterns:
        groups[normalize_error_pattern(p)].append(p)

    merged = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    n_old, n_new = len(old_patterns), len(groups)
    singletons = sum(1 for _, members in merged if len(members) == 1)

    lines = [
        '# Отчёт dry-run перегруппировки Groups',
        '',
        f'- Строк (старых шаблонов): **{n_old}**',
        f'- Групп после новой нормализации: **{n_new}** (−{(1 - n_new / n_old) * 100:.1f}%)',
        f'- Групп без склейки (1 старый шаблон): {singletons}',
        '',
        '## Топ-40 самых крупных склеек',
        '',
        'Проверьте глазами: всё внутри одной склейки должно быть ОДНОЙ и той же ошибкой.',
        '',
    ]
    for new_pattern, members in merged[:40]:
        if len(members) == 1:
            break
        total30 = sum(counts_30d.get(m, 0) for m in members)
        lines.append(f'### ← {len(members)} строк (за 30 дней: {total30})')
        lines.append(f'**Новый шаблон:** `{new_pattern[:300]}`')
        lines.append('')
        lines.append('Примеры старых шаблонов:')
        for m in members[:5]:
            lines.append(f'- `{m[:300]}`')
        if len(members) > 5:
            lines.append(f'- … и ещё {len(members) - 5}')
        lines.append('')

    report_path = os.path.abspath(report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'Старых шаблонов: {n_old} → новых групп: {n_new}')
    print(f'Отчёт: {report_path}')


if __name__ == '__main__':
    main()
