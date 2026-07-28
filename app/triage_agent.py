"""
Автономный агент ежедневного триажа логов (замена локальной рутины Claude).

Запускается по cron на сервере анализатора (bkp). Работает агентным циклом
с tool use через OpenAI API:
  1. Берёт из Groups группы «не обработано» + всплески (до MAX_GROUPS).
  2. Расследует: сырые примеры из Original data, полные логи/код на
     прод-сервере по SSH (СТРОГО только чтение), SELECT-only запросы в MySQL.
  3. Пишет вердикты в Groups (G–L) и дайджест дня в Digest (A1 дата, A2 текст).

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


def build_worklist(rows):
    """Группы на разбор: «не обработано» + всплески (×3 к среднему за 30д)."""
    work, spikes = [], []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) < 13 or not r[1].strip():
            continue
        d1 = int(r[2] or 0) if (r[2] or '').isdigit() else 0
        d30 = int(r[4] or 0) if (r[4] or '').isdigit() else 0
        item = {'row': i, 'id': r[12], 'pattern': r[1][:250], 'd1': d1,
                'd7': r[3], 'd30': d30, 'status': r[6].strip(),
                'verdict': r[7].strip(), 'action': r[10][:200]}
        if r[6].strip() == 'не обработано':
            work.append(item)
        elif d1 >= 10 and d30 > 0 and d1 >= 3 * max(d30 / 30.0, 1):
            spikes.append(item)
    is_err = lambda it: 'production.ERROR' in it['pattern']
    work.sort(key=lambda it: (not is_err(it), -it['d1']))
    return (work + spikes)[:MAX_GROUPS], len(work), len(spikes)


def get_examples(raw_logs_cache, pattern: str) -> str:
    samples = raw_logs_cache.get(pattern.strip(), [])
    if not samples:
        return '(сырых примеров за 30 дней не найдено — возможно, группа старая)'
    return '\n---\n'.join(f'[{d}] {t[:900]}' for d, t in samples[-3:])[:MAX_TOOL_OUTPUT]


# ===== Агентный цикл =====

TOOLS = [
    {"type": "function", "function": {
        "name": "get_examples",
        "description": "2–3 сырых примера логов группы из Original data (с живыми ID и значениями).",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Точный шаблон группы (колонка B)"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "prod_exec",
        "description": "Read-only команда на прод-сервере из каталога /var/www/app.sellerdata.ru. Разрешены grep/tail/head/cat/ls/wc/stat/find/awk/cut/sort/uniq и пайпы. Полные логи: storage/logs/laravel.log, storage/logs/account-<дата>.log. Код: app/...",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "prod_sql",
        "description": "Одиночный SELECT/SHOW/EXPLAIN в прод-БД appsellerdata (LIMIT добавится автоматически).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "write_verdict",
        "description": "Записать вердикт группе (строка листа Groups).",
        "parameters": {"type": "object", "properties": {
            "row": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["действовать", "понаблюдать", "игнорировать"]},
            "urgency": {"type": "string", "enum": ["сегодня", "неделя", "бэклог", ""]},
            "cause": {"type": "string", "enum": ["код", "данные", "инфра", "внешний сервис", "обрезки сообщений"]},
            "action": {"type": "string", "description": "1–3 предложения: что происходит и что делать"}},
            "required": ["row", "verdict", "cause", "action"]}}},
    {"type": "function", "function": {
        "name": "write_digest",
        "description": "Записать финальный дайджест дня (вкладка Digest). Вызывается ОДИН раз в самом конце.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
]


def system_prompt(today, sheet_id):
    base = f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={GROUPS_GID}&range=A'
    return f"""Ты — дежурный инженер сервиса sellerdata (аналитика маркетплейсов: синки Wildberries/Ozon, подписки, платежи Tinkoff/Forte). Сегодня {today}. Твоя задача: разобрать выданный список групп ошибок, поставить каждой вердикт (write_verdict) и в конце написать дайджест дня (write_digest).

РАССЛЕДОВАНИЕ. Сначала get_examples; если сути не видно — prod_exec (полный лог со стеком: grep -m3 -A15 'подстрока' storage/logs/laravel.log; код: cat/grep по путям из стеков) и prod_sql для проверки гипотез о данных. Не больше ~5 обращений на группу. Прод только для чтения. Если не уверен — вердикт «понаблюдать» с пометкой «низкая уверенность», не выдумывай.

ВЕРДИКТЫ. Одна первопричина у нескольких групп → одинаковый action с пометкой «один инцидент с #<ID главной группы>». Мусорные обрезки (обрывки с {{}}[] посреди шаблона, счётчики 0–2) → «игнорировать», cause «обрезки сообщений», без расследования. Группы «DATA: Unknown transaction type» — всегда «действовать»: достань конкретные operation_type и суммы из примеров.

ДАЙДЖЕСТ — строго по шаблону (человеческий рассказ, НЕ список групп):

🧾 <ДД.ММ> — что было важного

За сутки <N> логов. Главное: <одна фраза — самое важное и почему срочно>.

1️⃣ <2–4 фразы: что случилось, причинная цепочка, масштаб. Технические сущности в `моноширинном` виде.>
→ <Действие одной фразой.> [#<ID> · строка <row>]({base}<row>)

(3–6 пунктов по убыванию важности; нумерация ТОЛЬКО эмодзи 1️⃣2️⃣3️⃣…)

💸 Неучтённые операции: <если были DATA-логи за сутки: типы, количество, сумма ₽; иначе пункт не нужен> [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={UNKNOWN_TX_GID})

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
    model = os.environ.get('TRIAGE_MODEL') or config.get('openai_model', 'gpt-5.1')
    effort = os.environ.get('TRIAGE_EFFORT') or config.get('openai_reasoning_effort', 'low')

    ss = open_spreadsheet(config)

    today = datetime.now().strftime('%Y-%m-%d')
    try:
        digest_ws = ss.worksheet('Digest')
        if not test_mode and (digest_ws.acell('A1').value or '').strip() == today:
            logging.info('Дайджест за %s уже записан — выходим.', today)
            return
    except gspread.exceptions.WorksheetNotFound:
        digest_ws = ss.add_worksheet(title='Digest', rows='10', cols='2')

    groups_ws = ss.worksheet('Groups')
    rows = groups_ws.get_all_values()
    worklist, n_backlog, n_spikes = build_worklist(rows)
    total_1d = sum(int(r[2] or 0) for r in rows[1:] if len(r) > 2 and (r[2] or '').isdigit())
    bg_1d = sum(int(r[2] or 0) for r in rows[1:]
                if len(r) > 7 and (r[2] or '').isdigit() and r[7].strip().lower() == 'игнорировать')
    # Контекст: актуальные «действовать» (для блока «Со вчера» и связывания инцидентов)
    acting = [f"#{r[12]} строка {i}: [{r[2]}/1д] {r[1][:100]} | {r[10][:150]}"
              for i, r in enumerate(rows[1:], start=2)
              if len(r) > 12 and r[7].strip() == 'действовать' and int(r[2] or 0) > 0][:25]

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
        f"Групп на разбор: {len(worklist)} (бэклог: {n_backlog}, всплески: {n_spikes}).\n\n"
        "СПИСОК ГРУПП (row | id | за 1д/7д/30д | статус | старый вердикт | шаблон):\n" +
        '\n'.join(f"{it['row']} | #{it['id']} | {it['d1']}/{it['d7']}/{it['d30']} | {it['status']} | "
                  f"{it['verdict'] or '—'} | {it['pattern']}" for it in worklist) +
        "\n\nАКТУАЛЬНЫЕ «действовать» с активностью (контекст для дайджеста и связывания):\n" +
        ('\n'.join(acting) or '(нет)') +
        "\n\nРазбери группы и запиши вердикты, затем один раз вызови write_digest."
    )

    messages = [{"role": "system", "content": system_prompt(today, config['google_sheet_id'])},
                {"role": "user", "content": user_msg}]

    verdicts_written = 0
    digest_written = False
    test_verdicts = []
    usage = {'prompt': 0, 'completion': 0, 'cached': 0}
    create_kwargs = {'model': model, 'tools': TOOLS}
    if effort:
        create_kwargs['reasoning_effort'] = effort

    for step in range(MAX_STEPS):
        try:
            resp = client.chat.completions.create(messages=messages, **create_kwargs)
        except Exception as e:
            if 'reasoning_effort' in create_kwargs and 'reasoning' in str(e).lower():
                logging.warning('Модель не принимает reasoning_effort — повтор без него.')
                create_kwargs.pop('reasoning_effort')
                resp = client.chat.completions.create(messages=messages, **create_kwargs)
            else:
                raise
        if resp.usage:
            usage['prompt'] += resp.usage.prompt_tokens or 0
            usage['completion'] += resp.usage.completion_tokens or 0
            details = getattr(resp.usage, 'prompt_tokens_details', None)
            usage['cached'] += getattr(details, 'cached_tokens', 0) or 0
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            if digest_written:
                break
            messages.append({"role": "user", "content":
                             "Дайджест ещё не записан — заверши работу вызовом write_digest."})
            continue
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}
            if name == 'get_examples':
                result = get_examples(raw_cache, args.get('pattern', ''))
            elif name == 'prod_exec':
                result = tool_prod_exec(config, args.get('command', ''))
                logging.info('prod_exec: %s', args.get('command', '')[:200])
            elif name == 'prod_sql':
                result = tool_prod_sql(config, args.get('query', ''))
                logging.info('prod_sql: %s', args.get('query', '')[:200])
            elif name == 'write_verdict':
                row = int(args['row'])
                if test_mode:
                    test_verdicts.append(args)
                else:
                    groups_ws.batch_update([{
                        'range': f'G{row}:L{row}',
                        'values': [['обработано', args['verdict'], args.get('urgency', ''),
                                    args['cause'], args['action'][:900], today]]}])
                verdicts_written += 1
                result = f'ok, вердикт записан в строку {row}'
            elif name == 'write_digest':
                text = (args.get('text') or '').strip()[:3950]
                if len(text) < 200:
                    result = 'ОТКЛОНЕНО: дайджест подозрительно короткий, напиши полноценный.'
                elif test_mode:
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
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": str(result)[:MAX_TOOL_OUTPUT]})
        if digest_written and verdicts_written >= len(worklist):
            break

    logging.info('Готово: вердиктов %s/%s, дайджест: %s, шагов: %s',
                 verdicts_written, len(worklist), digest_written, step + 1)
    logging.info('USAGE model=%s effort=%s prompt=%s (cached=%s) completion=%s',
                 model, effort, usage['prompt'], usage['cached'], usage['completion'])
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
