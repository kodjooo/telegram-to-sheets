"""
Автономный агент ежедневного триажа логов (замена локальной рутины Claude).

Запускается по cron на сервере анализатора (bkp). Работает агентным циклом
с tool use через OpenAI API:
  1. Берёт из Groups группы «не обработано» + всплески (до MAX_GROUPS).
  2. Расследует: сырые примеры из Original data, полные логи/код на
     прод-сервере по SSH (СТРОГО только чтение), SELECT-only запросы в MySQL.
  3. Пишет вердикты в Groups (H–M) и дайджест дня в Digest (A1 дата, A2 текст).

Ограничители зашиты кодом, не промптом: белый список команд, запрет записи,
принудительный LIMIT в SQL, потолок шагов и размера выводов.

Идемпотентность: если в Digest уже стоит сегодняшняя дата — выходим сразу.
"""

import base64
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

import gspread
import httpx
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI

from normalize import merge_fragment_chains, normalize_error_pattern

BASE_DIR = '/app'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')
LOG_PATH = os.path.join(BASE_DIR, 'logs/triage_agent.log')
LOCK_PATH = os.path.join(BASE_DIR, 'triage_agent.lock')

MAX_GROUPS = 25          # групп за запуск
MAX_STEPS = 120          # шагов агентного цикла
MAX_TOOL_OUTPUT = 6000   # символов вывода инструмента
SSH_TIMEOUT = 30
GROUPS_GID = 1412410715
UNKNOWN_TX_GID = 1830882857

# Разрешённые команды на проде (только чтение)
ALLOWED_CMDS = ('grep', 'zgrep', 'egrep', 'tail', 'head', 'cat', 'ls', 'wc',
                'stat', 'find', 'date', 'awk', 'cut', 'sort', 'uniq')
FORBIDDEN_CHARS = re.compile(r'[;&`$><]')

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ===== Прод-сервер (только чтение) =====

def _ssh_run(config, remote_script: str) -> str:
    """Выполняет скрипт на проде через base64 (без проблем с кавычками)."""
    b64 = base64.b64encode(remote_script.encode()).decode()
    # -F /dev/null: смонтированный ~/.ssh принадлежит другому uid,
    # чужой config ssh-клиент отвергает — работаем без него, ключ явно.
    cmd = ['ssh', '-F', '/dev/null', '-i', '/root/.ssh/id_rsa',
           '-o', 'UserKnownHostsFile=/root/.ssh/known_hosts',
           '-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes',
           config.get('prod_ssh', 'app-dev@212.41.30.188'),
           f'echo {b64} | base64 -d | timeout {SSH_TIMEOUT} bash']
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT + 15)
        result = (out.stdout or '') + (('\n[stderr] ' + out.stderr.strip()) if out.stderr.strip() else '')
        return result[:MAX_TOOL_OUTPUT] or '(пустой вывод)'
    except subprocess.TimeoutExpired:
        return '(таймаут ssh)'


def tool_prod_exec(config, command: str) -> str:
    """Read-only команда на проде: grep/tail/cat/... с пайпами, без записи."""
    command = command.strip()
    if FORBIDDEN_CHARS.search(command):
        return 'ОТКЛОНЕНО: запрещённые символы (;&`$><). Только чтение, без перенаправлений.'
    for segment in command.split('|'):
        first = segment.strip().split(' ', 1)[0]
        if first not in ALLOWED_CMDS:
            return f'ОТКЛОНЕНО: команда «{first}» не в белом списке {ALLOWED_CMDS}.'
    return _ssh_run(config, f'cd /var/www/app.sellerdata.ru && {command}')


def tool_prod_sql(config, query: str) -> str:
    """SELECT-only запрос в прод-БД appsellerdata."""
    q = query.strip().rstrip(';').strip()
    if ';' in q:
        return 'ОТКЛОНЕНО: несколько запросов запрещены.'
    if not re.match(r'^(select|show|explain|describe)\b', q, re.IGNORECASE):
        return 'ОТКЛОНЕНО: разрешены только SELECT/SHOW/EXPLAIN/DESCRIBE.'
    if q.lower().startswith('select') and not re.search(r'\blimit\s+\d+', q, re.IGNORECASE):
        q += ' LIMIT 100'
    qb64 = base64.b64encode(q.encode()).decode()
    script = (
        'cd /var/www/app.sellerdata.ru && '
        'MYSQL_PWD=$(grep "^DB_PASSWORD=" .env | cut -d= -f2- | tr -d \'"\') '
        f'bash -c "echo {qb64} | base64 -d | mysql -u appuser -h localhost -D appsellerdata -t"'
    )
    return _ssh_run(config, script)


# ===== Google Sheets =====

def open_spreadsheet(config):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    return gspread.authorize(creds).open_by_key(config['google_sheet_id'])


def sheet_cols(rows):
    """Индексы колонок по именам заголовка (порядок колонок может меняться)."""
    header = rows[0] if rows else []
    idx = {name.strip(): i for i, name in enumerate(header) if name.strip()}

    def get(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and len(row) > i else ''

    return get


def build_worklist(rows, weekday=None):
    """Группы на разбор:
    1) «не обработано»;
    2) всплески (×3 к среднему за 30 дней);
    3) СИСТЕМНЫЕ ОТКАЗЫ — стабильно повторяющиеся каждый день группы
       (d7 ≈ 7×d1 и d30 ≈ 30×d1, разброс < 2×): гарантированный ежедневный
       отказ функции, который тонет в сортировке по объёму (например 5 падений
       в сутки семь дней подряд). Разбираем раз в неделю (по понедельникам),
       чтобы не жечь бюджет на группы, которые по определению не меняются."""
    col = sheet_cols(rows)
    if weekday is None:
        weekday = datetime.now().weekday()
    work, spikes, systemic = [], [], []
    for i, r in enumerate(rows[1:], start=2):
        pattern = col(r, 'Ошибка (шаблон)')
        if not pattern:
            continue
        to_int = lambda name: int(col(r, name)) if col(r, name).isdigit() else 0
        d1, d7, d30 = to_int('За 1 день'), to_int('За 7 дней'), to_int('За 30 дней')
        status = col(r, 'Статус')
        item = {'row': i, 'id': col(r, 'ID'), 'pattern': pattern[:250], 'd1': d1,
                'd7': d7, 'd30': d30, 'status': status,
                'verdict': col(r, 'Вердикт'), 'action': col(r, 'Действие')[:200],
                'acting_since': col(r, 'Впервые в действовать'), 'kind': 'бэклог'}
        if status == 'не обработано':
            work.append(item)
            continue
        if d1 >= 10 and d30 > 0 and d1 >= 3 * max(d30 / 30.0, 1):
            item['kind'] = 'всплеск'
            spikes.append(item)
            continue
        # Стабильный ежедневный отказ: счётчики кратны суточному без разброса
        if d1 > 0 and d7 >= 5 * d1 and d30 >= 20 * d1 and d7 <= 9 * d1:
            item['kind'] = 'системный отказ (стабильно каждый день)'
            systemic.append(item)
    is_err = lambda it: 'production.ERROR' in it['pattern']
    work.sort(key=lambda it: (not is_err(it), -it['d1']))
    systemic.sort(key=lambda it: -it['d30'])
    weekly = systemic[:5] if weekday == 0 else []
    return (work + spikes + weekly)[:MAX_GROUPS], len(work), len(spikes), len(weekly)


def get_examples(raw_logs_cache, pattern: str) -> str:
    samples = raw_logs_cache.get(pattern.strip(), [])
    if not samples:
        return '(сырых примеров за 30 дней не найдено — возможно, группа старая)'
    return '\n---\n'.join(f'[{d}] {t[:900]}' for d, t in samples[-3:])[:MAX_TOOL_OUTPUT]


# ===== Агентный цикл =====

# Формат Responses API: плоские function-инструменты со strict-схемами
TOOLS = [
    {"type": "function", "name": "get_examples", "strict": True,
     "description": "2–3 сырых примера логов группы из Original data (с живыми ID и значениями). ОБЯЗАТЕЛЕН перед вердиктом каждой активной группы.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"row": {"type": "integer", "description": "Номер строки группы из списка (первое поле)"}},
                    "required": ["row"]}},
    {"type": "function", "name": "prod_exec", "strict": True,
     "description": "Read-only команда на прод-сервере из /var/www/app.sellerdata.ru. Разрешены grep/tail/head/cat/ls/wc/stat/find/awk/cut/sort/uniq и пайпы. Полные логи со стеками: storage/logs/laravel.log, storage/logs/account-<дата>.log. Код приложения: app/...",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]}},
    {"type": "function", "name": "prod_sql", "strict": True,
     "description": "Одиночный SELECT/SHOW/EXPLAIN в прод-БД appsellerdata (LIMIT добавится автоматически).",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]}},
    {"type": "function", "name": "write_verdict", "strict": True,
     "description": "Записать вердикт группе (строка листа Groups). Для активных групп принимается только ПОСЛЕ расследования (минимум get_examples).",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {
                        "row": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["действовать", "понаблюдать", "игнорировать"]},
                        "urgency": {"type": "string", "enum": ["сегодня", "неделя", "бэклог", ""]},
                        "cause": {"type": "string", "enum": ["код", "данные", "инфра", "внешний сервис", "обрезки сообщений"]},
                        "action": {"type": "string", "description": "1–3 предложения: что происходит и что делать"}},
                    "required": ["row", "verdict", "urgency", "cause", "action"]}},
    {"type": "function", "name": "write_digest", "strict": True,
     "description": "Записать финальный дайджест дня (вкладка Digest). Вызывается ОДИН раз в самом конце, после всех вердиктов.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"]}},
]


def system_prompt(today, sheet_id):
    base = f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={GROUPS_GID}&range=A'
    return f"""Ты — дежурный инженер сервиса sellerdata (аналитика маркетплейсов: синки Wildberries/Ozon, подписки, платежи Tinkoff/Forte). Сегодня {today}. Твоя задача: разобрать выданный список групп ошибок, поставить каждой вердикт (write_verdict) и в конце написать дайджест дня (write_digest).

РАССЛЕДОВАНИЕ — ОБЯЗАТЕЛЬНО, вердикты без него отклоняются. Для КАЖДОЙ активной группы (за 1д > 0): сначала get_examples по номеру её строки. Для production.ERROR с 1д ≥ 10 — дополнительно prod_exec (полный лог со стеком: grep -m3 -A15 'подстрока' storage/logs/laravel.log) и при необходимости код (cat/grep по путям из стеков) и prod_sql для проверки гипотез о данных (например, failed_jobs после инцидентов с очередями). Ищи то, чего НЕТ в счётчиках: последствия (потерянные джобы, незаписанные данные), первопричины в коде, чувствительные данные в логах. Не больше ~5 обращений на группу. Прод только для чтения. Если не уверен — «понаблюдать» с пометкой «низкая уверенность», не выдумывай.

ДИСЦИПЛИНА ЦИФР (обязательно).
1) Любой счётчик — только с окном: запросы к failed_jobs и подобным накопительным таблицам обязаны иметь WHERE failed_at >= CURDATE() (или иное явно названное окно), и окно называется в тексте. failed_jobs не чистится годами — «остаются N джоб»/«накопилось N» без периода ЗАПРЕЩЕНЫ. Исторический контекст только так: «всего в таблице N с <дата>, из них за сутки — M»; в дайджест идёт M.
2) Крупный инцидент разложить до вывода о причине: если группа претендует на «действовать» И даёт >100 событий за сутки — обязательно GROUP BY LEFT(exception,90) (сколько разных типов отказа), GROUP BY HOUR(failed_at) (залп или растянуто), GROUP BY по аккаунту (сколько задето; если поля нет — сказать это). «Причина не подтверждена» допустима только после этих запросов и с указанием, что именно неясно.
3) Радиус — числом: в каждом инциденте строка эффекта в одном из видов: «задето аккаунтов: N» / «данные догрузились ретраями, потерь нет» / «радиус не измерен». Слова «массово», «многие», «тысячи» без числа рядом не использовать.
4) Ошибки в логе ≠ потеря данных: приоритет определяется потерей данных, а не числом строк лога. Если ретраи добили работу (за сутки в failed_jobs по этой джобе ~0 строк) — это НЕ инцидент, группа идёт в «Фон» с пометкой «шум: ERROR там, где нужен WARNING».
5) Роли источников: счётчики Groups — сигнал наличия и тренда (в таблицу попадает не весь лог, а то, что Zabbix переслал в Telegram; расхождение с продом в разы — норма, не баг сбора). Масштаб ВСЕГДА измеряется на проде. Не писать про «расхождение счётчиков» как про проблему и не сравнивать напрямую строки лога с записями failed_jobs.
6) «→ действие» — только то, что требует записи в прод, деплоя, правки кода или участия человека. Всё, что достигается чтением лога/БД/кода, выполняешь сам в ходе триажа, а результат пишешь в тело пункта.

7) Радиус измерять, а не отговариваться: аккаунт из джобы достаётся SQL — SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(payload,'"account_id\\":',-1),',',1) AS acc, COUNT(*) FROM failed_jobs WHERE failed_at >= CURDATE() GROUP BY acc (ключ может быть account_id/accountId/accountIds) — либо из лога: grep '<ошибка>' storage/logs/laravel.log | grep '<дата>' | awk -F'account_id":' '{{print $2}}' | cut -d, -f1 | sort | uniq -c. «Радиус не измерен» допустимо только после этих попыток и с указанием, что пробовал.
8) В каждом нумерованном пункте обязательна хотя бы одна цифра с окном и строка эффекта. Пункт без чисел в дайджест не идёт.
9) Если сам выяснил, что потерь нет (за сутки в failed_jobs ~0) — группа идёт в «Фон» одной строкой и НЕ занимает нумерованное место, сколько бы событий ни было.
10) Первопричину ищешь сам: «найти исходную ошибку»/«выяснить причину» в «→ действие» ЗАПРЕЩЕНЫ. До вердикта посмотри лог вокруг времени залпа (grep -B5 -A15 по метке времени первого отказа) и код по стеку; если лог ротирован — скажи это в теле пункта.

СИСТЕМНЫЕ ОТКАЗЫ. Группы с категорией отбора «системный отказ» — это стабильный ежедневный отказ функции (например ровно 5 падений в сутки много дней подряд, у одних и тех же аккаунтов). Объём мал, но это гарантированная поломка, а не транзиент: разбирай их так же серьёзно, как крупные инциденты, и в дайджесте давай отдельный пункт с явной формулировкой «повторяется каждый день, N/сутки».

ВЕРДИКТЫ. Одна первопричина у нескольких групп → одинаковый action с пометкой «один инцидент с #<ID главной группы>». Мусорные обрезки (обрывки с {{}}[] посреди шаблона, счётчики 0–2) → «игнорировать», cause «обрезки сообщений», без расследования. Группы «DATA: Unknown transaction type» — всегда «действовать»: достань из примеров КОНКРЕТНЫЕ названия типов (operation_type / supplier_oper_name / bonus_type_name) и количество логов по каждому за сутки.

ДАЙДЖЕСТ — строго по шаблону (человеческий рассказ, НЕ список групп):

🧾 <ДД.ММ> — что было важного

За сутки <N> логов. Главное: <одна фраза — самое важное и почему срочно>.

1️⃣ <2–4 фразы: что случилось, причинная цепочка, масштаб. Обязательна строка эффекта числом (задето аккаунтов: N / потерь нет / радиус не измерен) и окно у любых счётчиков. Технические сущности в `моноширинном` виде. Всё выясненное чтением — здесь, в теле пункта.>
→ <Действие одной фразой — только запись в прод/деплой/правка кода/участие человека.> [#<ID> · строка <row>]({base}<row>)

(3–6 пунктов по убыванию важности; нумерация ТОЛЬКО эмодзи 1️⃣2️⃣3️⃣…)

🔁 Висит без изменений: <одна строка на пункт: «<суть> — <N> дней, статус не менялся». Сюда выносятся группы с вердиктом «действовать», которые повторяются день за днём без изменения статуса: за сутки событий нет или ничего нового не выяснилось. Число дней = сегодня минус дата из колонки «Впервые в действовать» (передана в списке). Такой пункт НЕ занимает нумерованное место; возвращать его в нумерованные пункты только при изменении статуса или всплеске.>

💸 Неучтённые операции за сутки: <НазваниеТипа1> — <N> шт, <НазваниеТипа2> — <N> шт (ТОЛЬКО название типа и количество за сутки: без сумм, без аккаунтов, без сравнений с прошлыми днями и без истории. Если DATA-логов за сутки не было — пункт ВСЁ РАВНО оставить одной строкой: «💸 Неучтённые операции: за сутки не было.») [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={UNKNOWN_TX_GID})

Со вчера: <что починилось (с фактом), что не повторилось, что рецидивировало.>

Фон: ~<X> логов известного шума (вердикт «игнорировать»). Не оценено: <N>.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={GROUPS_GID})

Текст ≤ 3900 символов. Пустой день: «Ничего требующего внимания: только известный фон (X логов)» + блок «Со вчера». В дайджест включай и вчерашние ещё актуальные инциденты со срочностью «сегодня» (их видно в контексте), не только сегодняшние группы."""


def main():
    config = load_config()
    if not config.get('openai_api_key'):
        logging.error('openai_api_key не задан в config.json — агент не запущен.')
        return

    # Тестовый режим (сравнение моделей): ничего не пишем в таблицу,
    # вердикты и дайджест — в файлы logs/, идемпотентность отключена.
    test_mode = bool(os.environ.get('TRIAGE_TEST'))
    # Разовые прогоны: подменить день недели (для категории «системный отказ»,
    # которая штатно разбирается по понедельникам), разобрать ТОЛЬКО системные
    # отказы и не трогать вкладку Digest (вердикты при этом пишутся как обычно).
    weekday_override = os.environ.get('TRIAGE_WEEKDAY')
    only_systemic = bool(os.environ.get('TRIAGE_ONLY_SYSTEMIC'))
    no_digest = bool(os.environ.get('TRIAGE_NO_DIGEST'))
    model = os.environ.get('TRIAGE_MODEL') or config.get('openai_model', 'gpt-5.1')
    effort = os.environ.get('TRIAGE_EFFORT') or config.get('openai_reasoning_effort', 'low')

    ss = open_spreadsheet(config)

    today = datetime.now().strftime('%Y-%m-%d')
    try:
        digest_ws = ss.worksheet('Digest')
        if not test_mode and not no_digest and (digest_ws.acell('A1').value or '').strip() == today:
            logging.info('Дайджест за %s уже записан — выходим.', today)
            return
    except gspread.exceptions.WorksheetNotFound:
        digest_ws = ss.add_worksheet(title='Digest', rows='10', cols='2')

    groups_ws = ss.worksheet('Groups')
    rows = groups_ws.get_all_values()
    worklist, n_backlog, n_spikes, n_systemic = build_worklist(
        rows, weekday=int(weekday_override) if weekday_override else None)
    if only_systemic:
        worklist = [it for it in worklist if 'системный' in it['kind']]
        n_backlog = n_spikes = 0
    col = sheet_cols(rows)
    d1_of = lambda r: int(col(r, 'За 1 день')) if col(r, 'За 1 день').isdigit() else 0
    total_1d = sum(d1_of(r) for r in rows[1:])
    bg_1d = sum(d1_of(r) for r in rows[1:] if col(r, 'Вердикт').lower() == 'игнорировать')
    # Контекст: актуальные «действовать» (для блока «Со вчера» и связывания инцидентов)
    acting = [f"#{col(r, 'ID')} строка {i}: [{d1_of(r)}/1д] "
              f"в «действовать» с {col(r, 'Впервые в действовать') or '?'} | "
              f"{col(r, 'Ошибка (шаблон)')[:100]} | {col(r, 'Действие')[:150]}"
              for i, r in enumerate(rows[1:], start=2)
              if col(r, 'Вердикт') == 'действовать'][:30]

    # Кэш сырых примеров
    raw_rows = ss.worksheet('Original data').get_all_values()
    logs = [{'id': int(r[0]), 'date': r[1], 'text': r[2]}
            for r in raw_rows[1:] if len(r) >= 3 and r[0].strip().isdigit()]
    raw_cache = defaultdict(list)
    for m in merge_fragment_chains(logs):
        p = normalize_error_pattern(m['text'])[:250]
        raw_cache[p].append((m['date'], m['text']))

    # OpenAI блокирует регион сервера — ходим через локальный прокси-пул
    # (тот же, что для Telegram). Отключается пустым значением openai_proxy.
    proxy = config.get('openai_proxy', 'socks5://127.0.0.1:8080')
    http_client = httpx.Client(proxy=proxy, timeout=180) if proxy else None
    client = OpenAI(api_key=config['openai_api_key'], http_client=http_client)
    logging.info('Модель: %s, effort: %s, test_mode: %s, proxy: %s',
                 model, effort, test_mode, bool(proxy))

    user_msg = (
        f"Всего логов за сутки: {total_1d}, из них известный фон: {bg_1d}.\n"
        f"Групп на разбор: {len(worklist)} (бэклог: {n_backlog}, всплески: {n_spikes}, системные отказы: {n_systemic}).\n\n"
        "СПИСОК ГРУПП (row | id | за 1д/7д/30д | категория отбора | статус | старый вердикт | шаблон):\n" +
        '\n'.join(f"{it['row']} | #{it['id']} | {it['d1']}/{it['d7']}/{it['d30']} | {it['kind']} | "
                  f"{it['status']} | {it['verdict'] or '—'} | {it['pattern']}" for it in worklist) +
        "\n\nАКТУАЛЬНЫЕ «действовать» с активностью (контекст для дайджеста и связывания):\n" +
        ('\n'.join(acting) or '(нет)') +
        "\n\nРазбери группы и запиши вердикты, затем один раз вызови write_digest."
    )

    input_list = [{"role": "developer", "content": system_prompt(today, config['google_sheet_id'])},
                  {"role": "user", "content": user_msg}]

    verdicts_written = 0
    digest_written = False
    test_verdicts = []
    usage = {'prompt': 0, 'completion': 0, 'cached': 0, 'reasoning': 0}
    # Принуждение к расследованию: активная группа (1д>0) не получит вердикт,
    # пока по ней не запрошены сырые примеры. Учёт — по номеру строки
    # (текст шаблона модели передают неточно, это вызывало циклы отказов).
    worklist_by_row = {it['row']: it for it in worklist}
    investigated = set()  # номера строк, по которым был get_examples
    MAX_PROMPT_BUDGET = 2_000_000  # потолок входных токенов на прогон

    kwargs = {'model': model, 'tools': TOOLS, 'max_output_tokens': 30000}
    if effort:
        kwargs['reasoning'] = {'effort': effort}

    for step in range(MAX_STEPS):
        resp = client.responses.create(input=input_list, **kwargs)
        if resp.usage:
            usage['prompt'] += resp.usage.input_tokens or 0
            usage['completion'] += resp.usage.output_tokens or 0
            in_det = getattr(resp.usage, 'input_tokens_details', None)
            usage['cached'] += getattr(in_det, 'cached_tokens', 0) or 0
            out_det = getattr(resp.usage, 'output_tokens_details', None)
            usage['reasoning'] += getattr(out_det, 'reasoning_tokens', 0) or 0

        # Возвращаем модели ВЕСЬ вывод, включая reasoning-блоки (требование
        # Responses API для reasoning-моделей с инструментами)
        input_list += resp.output

        calls = [item for item in resp.output if item.type == 'function_call']
        if not calls:
            if digest_written:
                break
            input_list.append({"role": "user", "content":
                               "Дайджест ещё не записан — заверши работу вызовом write_digest."})
            continue

        for tc in calls:
            name = tc.name
            try:
                args = json.loads(tc.arguments or '{}')
            except json.JSONDecodeError:
                args = {}
            if name == 'get_examples':
                row = int(args.get('row') or 0)
                item = worklist_by_row.get(row)
                if item is None:
                    result = f'Строки {row} нет в рабочем списке.'
                else:
                    investigated.add(row)
                    result = get_examples(raw_cache, item['pattern'])
            elif name == 'prod_exec':
                result = tool_prod_exec(config, args.get('command', ''))
                logging.info('prod_exec: %s', args.get('command', '')[:200])
            elif name == 'prod_sql':
                result = tool_prod_sql(config, args.get('query', ''))
                logging.info('prod_sql: %s', args.get('query', '')[:200])
            elif name == 'write_verdict':
                row = int(args['row'])
                item = worklist_by_row.get(row)
                needs_investigation = (
                    item is not None and item['d1'] > 0
                    and args.get('cause') != 'обрезки сообщений'
                    and row not in investigated
                )
                if needs_investigation:
                    result = ('ОТКЛОНЕНО: активная группа без расследования. Сначала '
                              'вызови get_examples для её шаблона (и prod_exec для '
                              'production.ERROR), затем повтори write_verdict.')
                else:
                    if test_mode:
                        test_verdicts.append(args)
                    else:
                        groups_ws.batch_update([{
                            'range': f'H{row}:M{row}',
                            'values': [['обработано', args['verdict'], args.get('urgency', ''),
                                        args['cause'], args['action'][:900], today]]}])
                    verdicts_written += 1
                    result = f'ok, вердикт записан в строку {row}'
            elif name == 'write_digest':
                text = (args.get('text') or '').strip()[:3950]
                budget_exceeded = usage['prompt'] > MAX_PROMPT_BUDGET
                if len(text) < 200:
                    result = 'ОТКЛОНЕНО: дайджест подозрительно короткий, напиши полноценный.'
                elif verdicts_written < len(worklist) and not budget_exceeded:
                    result = (f'ОТКЛОНЕНО: вердикты записаны только для {verdicts_written} '
                              f'из {len(worklist)} групп — сначала заверши триаж.')
                elif test_mode or no_digest:
                    path = os.path.join(BASE_DIR, f'logs/digest_test_{model}_{effort}.md')
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(text + '\n\n===ВЕРДИКТЫ===\n' +
                                json.dumps(test_verdicts, ensure_ascii=False, indent=1))
                    digest_written = True
                    result = f'ok, дайджест записан (тест: {path})'
                else:
                    digest_ws.update(values=[[today], [text]], range_name='A1:A2')
                    digest_written = True
                    result = 'ok, дайджест записан'
            else:
                result = f'неизвестный инструмент {name}'
            input_list.append({"type": "function_call_output", "call_id": tc.call_id,
                               "output": str(result)[:MAX_TOOL_OUTPUT]})
        if digest_written:
            break
        if usage['prompt'] > MAX_PROMPT_BUDGET:
            logging.warning('Бюджет токенов исчерпан (%s) — требуем немедленный дайджест.',
                            usage['prompt'])
            input_list.append({"role": "user", "content":
                               "БЮДЖЕТ ИСЧЕРПАН. Прекрати расследования и немедленно "
                               "вызови write_digest с тем, что уже известно."})

    logging.info('Готово: вердиктов %s/%s, дайджест: %s, шагов: %s',
                 verdicts_written, len(worklist), digest_written, step + 1)
    logging.info('USAGE model=%s effort=%s prompt=%s (cached=%s) completion=%s (reasoning=%s)',
                 model, effort, usage['prompt'], usage['cached'],
                 usage['completion'], usage['reasoning'])
    if not digest_written:
        logging.error('Агент завершился БЕЗ дайджеста — сводка сегодня не уйдёт.')


if __name__ == '__main__':
    lock = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info('Предыдущий прогон triage_agent ещё работает — выходим.')
        sys.exit(0)
    try:
        main()
    except Exception as e:
        logging.error('Ошибка triage_agent: %s', e, exc_info=True)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
