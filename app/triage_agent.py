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

import fcntl
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

import gspread
import httpx
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI

from normalize import merge_fragment_chains, normalize_error_pattern
from unknown_tx_stats import aggregate_unknown_transactions, format_unknown_tx_summary
from triage_context import is_monitoring, format_examples, watching_block
from triage_monitoring import check_weekly_monitoring

BASE_DIR = os.environ.get('APP_DIR', '/app')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')
LOG_PATH = os.path.join(BASE_DIR, 'logs/triage_agent.log')
LOCK_PATH = os.path.join(BASE_DIR, 'triage_agent.lock')
PROD_KNOWN_HOSTS_PATH = os.path.join(BASE_DIR, 'prod_known_hosts')
DEFAULT_PROD_SSH = 'app-dev@app.sellerdata.ru'

MAX_GROUPS = 25          # групп за запуск
MAX_STEPS = 120          # шагов агентного цикла
MAX_TOOL_OUTPUT = 6000   # символов вывода инструмента
SSH_TIMEOUT = 30
GROUPS_GID = 1412410715
UNKNOWN_TX_GID = 1830882857

PROD_ACCESS_FAILURES = ('ошибка транспорта обёртки', 'host key verification failed', 'permission denied', 'could not resolve hostname')

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def production_access_failed(result: str) -> bool:
    """Определяет отказ транспорта, при котором данные production недостоверны."""
    return '(таймаут ssh)' in result or any(marker in result.lower()
                                          for marker in PROD_ACCESS_FAILURES)


def build_degraded_digest(today: str, total_1d: int, bg_1d: int, groups_count: int) -> str:
    """Короткий безопасный отчёт, когда production нельзя перепроверить."""
    return (
        f'🧾 {datetime.strptime(today, "%Y-%m-%d").strftime("%d.%m")} — '
        'проверка production недоступна\n\n'
        'Окно: последние 24 часа; точные границы и данные production не сняты.\n\n'
        f'За окно {total_1d} событий по нашим группам, из них известный фон — {bg_1d}. '
        f'Сигналов для разбора — {groups_count}.\n\n'
        'Сводка не подтверждает инциденты и не публикует действия: доступ к production '
        'недоступен, поэтому нельзя проверить логи, очередь и реальный радиус ошибок. '
        'Нужно восстановить доступ и сформировать сводку повторно.\n\n'
        '👉 [Вся таблица](https://docs.google.com/spreadsheets/)'
    )


# ===== Прод-сервер (только чтение) =====

def prod_gate(config, op, **fields):
    cmd = [
        'ssh', '-T', '-F', '/dev/null', '-i', '/root/.ssh-triage/id_ed25519',
        '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=10',
        '-o', f'UserKnownHostsFile={config.get("prod_known_hosts", PROD_KNOWN_HOSTS_PATH)}',
        config.get('prod_ssh', DEFAULT_PROD_SSH),
    ]
    request = {key: value for key, value in fields.items() if value is not None}
    try:
        out = subprocess.run(cmd, input=json.dumps({'op': op, **request}),
                             capture_output=True, text=True, timeout=SSH_TIMEOUT + 15)
        response = json.loads(out.stdout)
        if not isinstance(response, dict) or 'ok' not in response:
            raise ValueError('invalid gateway response')
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        logging.error('prod_gate: %s, транспорт: %s', op, type(exc).__name__)
        return 'Production недоступен: ошибка транспорта обёртки.'
    logging.info('prod_gate: %s, ok=%s, rc=%d', op, response['ok'], out.returncode)
    if not response['ok']:
        return f'Обёртка отклонила запрос: {response.get("error", "причина не указана")}'[:MAX_TOOL_OUTPUT]
    if out.returncode:
        return 'Production недоступен: ошибка транспорта обёртки.'
    return json.dumps(response, ensure_ascii=False)[:MAX_TOOL_OUTPUT]


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
    work, spikes, systemic, monitoring = [], [], [], []
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
        item['monitoring'] = is_monitoring(pattern, col(r, 'Категория'))
        if item['monitoring'] and d1 > 0:
            item['kind'] = 'MONITORING: обязательная перепроверка'
            monitoring.append(item)
            continue
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
    monitoring.sort(key=lambda it: -it['d1'])
    return (monitoring + work + spikes + weekly)[:MAX_GROUPS], len(work) + len(monitoring), len(spikes), len(weekly)


def get_examples(raw_logs_cache, pattern: str, monitoring=False) -> str:
    samples = raw_logs_cache.get(pattern.strip(), [])
    return format_examples(samples, monitoring=monitoring or is_monitoring(pattern), maxchars=MAX_TOOL_OUTPUT)


def monitoring_downgrade_check(config, item, verdict, raw_cache, now):
    """Не доверяем обещанию модели проверить восстановление MONITORING."""
    if not item or not item.get('monitoring') or verdict == 'действовать':
        return None
    if '[weekly-mail]' not in item['pattern'].lower():
        return {'verified': False, 'reason': 'Для этого MONITORING нет автоматической проверки контекста. Сохрани «действовать»: требуется проверка, инцидент ещё не подтверждён.'}
    access_failed = False

    def query(query_text):
        nonlocal access_failed
        response = prod_gate(config, 'sql', query=query_text)
        access_failed = access_failed or production_access_failed(response)
        return response

    result = check_weekly_monitoring(raw_cache.get(item['pattern'].strip(), []), now, query)
    result['production_unavailable'] = access_failed
    return result


def attach_watching_block(text, block):
    # Удаляем вариант модели целиком: authoritative список строится из Groups.
    text = re.sub(r'^\s*👀[^\n]*(?:\n(?!\s*(?:🔁|💸|Со вчера:|Фон:|👉|[1-9]️⃣)).*)*',
                  '', text, flags=re.MULTILINE).strip()
    block = block or '👀 Наблюдаем: активных групп нет.'
    marker = re.search(r'^(?:🔁|💸|Со вчера:|Фон:|👉)', text, flags=re.MULTILINE)
    if marker:
        return text[:marker.start()].rstrip() + '\n\n' + block + '\n\n' + text[marker.start():]
    return text + '\n\n' + block


# ===== Агентный цикл =====

def gate_tool(op, description, required=(), **fields):
    return {
        "type": "function", "name": f"prod_{op}", "description": description, "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False, "required": list(fields),
            "properties": {name: {"type": kind if name in required else [kind, "null"]}
                           for name, kind in fields.items()},
        },
    }


# Формат Responses API: плоские function-инструменты со strict-схемами
TOOLS = [
    {"type": "function", "name": "get_examples", "strict": True,
     "description": "2–3 сырых примера логов группы из Original data (с живыми ID и значениями). ОБЯЗАТЕЛЕН перед вердиктом каждой активной группы.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"row": {"type": "integer", "description": "Номер строки группы из списка (первое поле)"}},
                    "required": ["row"]}},
    gate_tool("time", "Время production с часовым поясом."),
    gate_tool("version", "Текущий SHA и история выкладок. since в МСК, YYYY-MM-DD HH:MM.",
              since="string"),
    gate_tool("grep", "Поиск по laravel.log (files=laravel) или account-логам (files=account, from/to — дни). "
              "rotations 0–10, контекст before/after 0–50, max 1–500 на файл. "
              "count считает все совпадения, fixed выбирает буквальный поиск. "
              "Для подсчёта аккаунтов используй SQL или извлеки ID из найденных строк.",
              required=("pattern",), pattern="string", files="string", rotations="integer",
              **{"from": "string", "to": "string"}, fixed="boolean", ignore_case="boolean",
              before="integer", after="integer", max="integer", count="boolean"),
    gate_tool("read", "PHP-исходник из app/, routes/, config/, database/ или resources/views/. До 400 строк.",
              required=("path",), path="string", **{"from": "integer", "to": "integer"}),
    gate_tool("sql", "Один запрос чтения к разрешённым данным production. До 1000 строк, 30 секунд.",
              required=("query",), query="string"),
    {"type": "function", "name": "write_verdict", "strict": True,
     "description": "Записать вердикт группе из рабочего списка. Для активных групп нужен get_examples. MONITORING до проверки — действовать (нужно проверить, не утверждать инцидент). Понижение weekly-mail дополнительно проверяется кодом по всем точным statistics_id/data_source; нет прав или не доказана доставка — понижение отклоняется.",
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

РАССЛЕДОВАНИЕ — ОБЯЗАТЕЛЬНО, вердикты без него отклоняются. Для КАЖДОЙ активной группы (за 1д > 0): сначала get_examples по номеру её строки. Для production.ERROR с 1д ≥ 10 — дополнительно prod_grep (pattern, max=3, after=15, rotations=1) и при необходимости prod_read по путям из стеков и prod_sql для проверки гипотез о данных (например, failed_jobs после инцидентов с очередями). Ищи то, чего НЕТ в счётчиках: последствия (потерянные джобы, незаписанные данные), первопричины в коде, чувствительные данные в логах. Не больше ~5 обращений на группу. Прод только для чтения. Если не уверен — «понаблюдать» с пометкой «низкая уверенность», не выдумывай.

ДИСЦИПЛИНА ЦИФР (обязательно).
1) Любой счётчик — только с окном: запросы к failed_jobs и подобным накопительным таблицам обязаны иметь WHERE failed_at >= NOW() - INTERVAL 24 HOUR (или иное явно названное окно), и окно называется в тексте. failed_jobs не чистится годами — «остаются N джоб»/«накопилось N» без периода ЗАПРЕЩЕНЫ. Исторический контекст только так: «всего в таблице N с <дата>, из них за сутки — M»; в дайджест идёт M.
2) Крупный инцидент разложить до вывода о причине: если группа претендует на «действовать» И даёт >100 событий за сутки — обязательно GROUP BY LEFT(exception,90) (сколько разных типов отказа), GROUP BY HOUR(failed_at) (залп или растянуто), измерение аккаунтов рабочим запросом из правила 7 (если не извлечены — привести фактический результат по правилу 7). «Причина не подтверждена» допустима только после этих запросов и с указанием, что именно неясно.
3) Радиус — числом: в каждом инциденте строка эффекта в одном из видов: «задето аккаунтов: N» / «данные догрузились ретраями, потерь нет» / «радиус не измерен». Слова «массово», «многие», «тысячи» без числа рядом не использовать.
4) Ошибки в логе ≠ потеря данных: приоритет определяется потерей данных, а не числом строк лога. Если восстановление подтверждено текущими данными (для плановых синков — account_sync_schedule.last_success_at после отказа; один лишь ноль строк в failed_jobs потерь не исключает) — это НЕ инцидент, группа идёт в «Фон» с пометкой «шум: ERROR там, где нужен WARNING».
5) Роли источников: счётчики Groups — сигнал наличия и тренда (в таблицу попадает не весь лог, а то, что Zabbix переслал в Telegram; расхождение с продом в разы — норма, не баг сбора). Масштаб ВСЕГДА измеряется на проде. Не писать про «расхождение счётчиков» как про проблему и не сравнивать напрямую строки лога с записями failed_jobs.
6) «→ действие» — только то, что требует записи в прод, деплоя, правки кода или участия человека. Всё, что достигается чтением лога/БД/кода, выполняешь сам в ходе триажа, а результат пишешь в тело пункта. Исключение: для непроверенного активного MONITORING сохраняй «действовать» с действием «проверить по точному контексту», пока проверка недоступна или не завершена; это запрос проверки, а не подтверждение инцидента.

7) Радиус измерять рабочим запросом, а не отговариваться. В JSON payload поле data.command содержит PHP-сериализованный объект: accountId";i:10104 (или account_id), перед именем свойства может быть \\u0000 — маркер private/protected свойства. Это не JSON-поле account_id: поиск только по JSON-ключу пропускает аккаунты. Обязательный SQL за текущее окно: SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(payload,'"displayName":"',-1),'"',1) AS cls, COUNT(*) AS fails, COUNT(DISTINCT REGEXP_SUBSTR(REGEXP_SUBSTR(payload,'account_?[Ii]d...{{1,4}}i:[0-9]+'),'[0-9]+$')) AS accounts FROM failed_jobs WHERE failed_at >= NOW() - INTERVAL 24 HOUR GROUP BY cls. Поле accounts — число различных аккаунтов класса, не сумма по классам. «Радиус не измерен» допустимо ТОЛЬКО если этот запрос вернул пустой/недоступный результат либо accounts=0 при fails>0 (аккаунты не извлечены); обязательно привести фактический результат: «0 строк», «ошибка <текст>» или «fails=N, accounts=0». Не подменять выполненный запрос обещанием проверить или перечнем предполагаемых ключей. Ошибка доступа к production подчиняется РЕЖИМУ ДЕГРАДАЦИИ.
8) В каждом нумерованном пункте обязательна хотя бы одна цифра с окном и строка эффекта. Пункт без чисел в дайджест не идёт.
9) Если сам подтвердил восстановление и отсутствие потерь (для плановых синков — успешный запуск после отказа по account_sync_schedule.last_success_at) — группа идёт в «Фон» одной строкой и НЕ занимает нумерованное место, сколько бы событий ни было.
10) Первопричину ищешь сам: «найти исходную ошибку»/«выяснить причину» в «→ действие» ЗАПРЕЩЕНЫ. До вердикта посмотри лог вокруг времени залпа (prod_grep по метке времени первого отказа, before=5, after=15) и код по стеку; если лог ротирован — скажи это в теле пункта.

ОБЯЗАТЕЛЬНЫЕ ДАННЫЕ ОТЧЁТА (снимать каждый прогон).
О1) Одно окно на весь отчёт — ПОСЛЕДНИЕ 24 ЧАСА (от «сейчас минус 24 часа» до момента составления, например 06:30 вчера — 06:30 сегодня). Календарные сутки НЕ использовать: отчёт выходит рано утром и терял бы почти весь прошедший день. Окно называется в заголовке; окно называется в заголовке, все числа отчёта из него. Счётчики Groups (скользящие 24ч) — только для отбора групп, в текст идут числа с прода и из Original data по датам. Не смешивать окна внутри пункта.
О2) Таблица классов отказов ОБЯЗАТЕЛЬНА — не выборка «интересного», а все классы за окно с долями (топ-8 + строка «прочее»). Запрос: SELECT FLOOR(TIMESTAMPDIFF(HOUR, failed_at, NOW())/24) AS bucket, SUBSTRING_INDEX(SUBSTRING_INDEX(payload,'"displayName":"',-1),'"',1) cls, COUNT(*) n FROM failed_jobs WHERE failed_at >= NOW() - INTERVAL 96 HOUR GROUP BY bucket, cls ORDER BY bucket, n DESC — bucket=0 это окно отчёта (последние 24 часа), 1/2/3 предыдущие сутки: один запрос даёт и доли за окно, и тренд из четырёх точек. Порядок нумерованных пунктов СЛЕДУЕТ ИЗ ТАБЛИЦЫ, а не из впечатления. Если запрос вернул пусто/мусор — написать «таблица классов недоступна», не пропускать блок молча. Класс, который за окно дал 0, но в тренде за 4 дня имел заметные значения (например 87·0·0·0), ОБЯЗАН остаться строкой таблицы со статусом «закрыто релизом <хеш, дата>» или «не повторяется N дней» — иначе через неделю никто не вспомнит, чинили это или прошло само.
О3) Сверка с релизами за окно ОБЯЗАТЕЛЬНА: вызови prod_time и prod_version с началом окна в since. Для факта выкладки используй deploys и head. Класс исчез после выката → «закрыто релизом <хеш, дата>», а не «не повторилось». Класс появился после выката → РЕГРЕСС, важнее всего остального в выпуске.

РЕЖИМ ДЕГРАДАЦИИ. Если любой инструмент production вернул ошибку SSH/host key/авторизации/таймаут, production недоступен. В таком случае не делай выводов о причинах, потерях, числе окончательных отказов и не предлагай действий над сервисами: сведения из Groups — только сигналы. Вызови `write_digest`; система заменит его на безопасное уведомление о недоступности проверки. Вердикты для активных групп не записывай; существующее «действовать» у непроверенного активного MONITORING сохраняется, запрет доступа не даёт оснований его понизить.
О4) Тренд из четырёх точек (сегодня · вчера · позавчера · три дня назад) для каждой строки таблицы и для лог-групп в пунктах, например 4 · 6 · 7 · 23 ▲. Для лог-групп считать посуточно из Original data по датам, не из Groups.
О5) Перепроверять свежесть перед публикацией: для каждого пункта с призывом к действию над конкретным объектом (аккаунт, джоба, файл) снять текущее состояние объекта. Если проблема уже ушла — пункт остаётся с пометкой «на момент отчёта устранено (<факт>)» и БЕЗ призыва.
О6) Наблюдение и гипотезу держать раздельно: «Видно: <факт> · Возможная причина: <гипотеза> · Проверить: <что смотреть>».
О7) В каждой рекомендации критерий проверки: по какому числу через сутки будет видно, помогло ли (например: после правки CalculateAccountDayJob в failed_jobs < 3 за сутки).
О8) Пункты с общей причиной склеивать: один инцидент — один пункт, симптомы внутри.

О9) Статусы в таблице классов — ТОЛЬКО из словаря: действовать / наблюдать / фон / закрыто релизом <хеш> / не повторяется N дней. Свободные формулировки («разбор готов», «403 Ozon», «действует») запрещены.
О10) Строка «прочее» — только для классов, у которых были события за окно. Класс, упавший до нуля, выносится ОТДЕЛЬНОЙ именованной строкой со своим трендом и статусом, а не прячется в «прочее». Числа в тексте обязаны совпадать с числами таблицы.
О11) Для радиуса обязательно выполнить SQL из правила 7. «Аккаунты не извлекались в этом прогоне» и перечисление возможных ключей вместо результата запрещены. Либо число accounts из текущего окна, либо «радиус не измерен» строго при пустом/недоступном результате извлечения с фактом результата, как требует правило 7.

О12) Время — только из системных часов (`prod_time`), НЕ из отметки git-коммита или строки лога, и всегда в МСК. Для git ОБЯЗАТЕЛЬНО --date=format-local:'%Y-%m-%d %H:%M' — при --date=format: время показывается в зоне автора коммита и уезжает вперёд. Конец окна в заголовке = фактическое время составления отчёта.
О13) Блок 🔁 и таблица не должны противоречить: класс, уже показанный в таблице как затихший, в 🔁 не дублировать. Если возраст вердикта и возраст последнего события разные — писать обе величины явно («вердикт держится 18 дней, событий нет 3 дня»). Числительные согласовывать: «3 дня», не «3 дней».

О14) ПИСАТЬ ДЛЯ НЕТЕХНИЧЕСКОГО ЧИТАТЕЛЯ — отчёт читают не только инженеры.
- Заголовок «Главное» — без технических терминов и имён классов: что сломалось с точки зрения дела («данные по заказам одного магазина не досчитались»).
- Каждый нумерованный пункт НАЧИНАЕТСЯ с простой фразы: что не работает и чем это грозит (какие данные не обновились, что не увидит клиент, где деньги). Технические подробности — имена джоб, полей, кодов — идут ПОСЛЕ, как обоснование, в моноширинном виде.
- Жаргон без расшифровки запрещён: «финальные отказы», «stalled-тики», «hard-fail» (окончательный отказ авторизации), «gate», «working-счётчик», «bulk update», «lock wait», «fallback-разнесение», «aggregate» и подобное — либо заменять простыми словами, либо давать пояснение рядом («джоба окончательно упала — все попытки исчерпаны», «блокировка в базе: две операции писали одни и те же строки»).
- Строка «→ действие» — обычным языком, понятным менеджеру.
- Блоки «Со вчера», 🔁 и 💸 — тем же простым языком: не «109 XJob не повторились», а «вчера проверка отчётов падала 109 раз, сегодня — ни разу».
- Таблица классов остаётся технической: это справочник для инженера.

О15) Не выдавать артефакты разбора за названия: если тип операции не распознан (file-unknown, «—», пусто) — писать «N логов без распознанного типа операции», а не как имя типа.
О16) Радиус — только за текущее окно. Числа из прежних прогонов («ранее измерено 48 аккаунтов») в отчёт НЕ переносить: это смешение двух окон в одном пункте. Либо измерить сейчас SQL из правила 7, либо «радиус в этом окне не измерен» только при фактически пустом/недоступном результате извлечения с указанием результата по правилу 7.
О17) Шапка колонки в таблице — «24ч», а не «Сег»: окно скользящее.

О18) Заголовок «Главное» и строка «→ действие» — БЕЗ жаргона вообще: hard_fail → «окончательный отказ входа», invalid_grant → «ключ отозван», «неконсистентные платежи» → «платежи с незаполненным тарифом», «нераспределяемые кампании» → «кампании без привязки к товару». Внутри пункта термин допустим с пояснением.
О19) Пересекающиеся множества не складывать: писать «16 и 15 аккаунтов (пересечение не проверялось)», а не «минимум 31».

О20) Названия операций — ДОСЛОВНО из лога: в пункте 💸 писать operation_type ровно как пришёл (OzonAccrualNON_ITEMType121, OzonAccrualUnknownCategoryCONTAINER_FEES) и количество. Не переписывать, не расшифровывать, не подставлять свои описания — номер типа в самом значении уже есть. Допустима одна фактическая пометка («имя синтезирует наш адаптер, у Ozon такого нет»), но идентификатор менять нельзя.

ПЛАНОВЫЕ СИНКИ. SyncOrdersJob, TransactionOnlineJob, SyncTransactionsJob, SyncProductsJob, SyncWarehousesJob с 21.11.2026 автоматически перепланируются планировщиком через 1–6 часов. Отказ в failed_jobs для них сам по себе не означает остановку синка или потерю данных. Единичные upstream 5xx и таймауты → «понаблюдать»: не предлагай retry failed_jobs, queue:retry или добавление ретраев. «Действовать» — только если проблема повторяется второй день у тех же аккаунтов либо подтверждён рост отказов у тех же аккаунтов; сравни конкретные аккаунты и окна. Перед выводом о невосстановлении проверь account_sync_schedule.last_success_at для соответствующего аккаунта и типа синка: успех ПОСЛЕ отказа подтверждает восстановление. Не выполненный/недоступный SELECT и отсутствие проверки не доказывают зависание; при недостатке данных — «понаблюдать», низкая уверенность, без утверждения о потерях. Эти правила приоритетнее общего правила о системных отказах и сортировки по объёму failed_jobs.

MONITORING. Все активные сигналы MONITORING намеренные: ДО успешной проверки на production по точному контексту события статус — «действовать», действие — «проверить по точному контексту». Непроверенный сигнал не называть подтверждённым инцидентом и не утверждать потерю данных или зависание. Понижать до «понаблюдать» или «игнорировать» допустимо ТОЛЬКО после успешной проверки именно этого объекта и источника с фактом результата. Контекст содержит statistics_id (также может называться stats_id) и data_source: сопоставь statistics_id с account_report_statistics.id, а строки данных — по account_report_statistics_data.account_report_statistics_id; data_source проверяй по точному контексту события. Не смешивай statistics_id/stats_id, account_id, ID строк данных, разные data_source и разные события. При запрете SELECT, отсутствии доступа или незавершённой проверке сохраняй «действовать»: невозможность проверить — причина проверки человеком, а не доказательство восстановления; укажи фактический отказ проверки. При общей недоступности production соблюдай РЕЖИМ ДЕГРАДАЦИИ без понижения существующего статуса. Это правило приоритетнее общих правил «при неуверенности — понаблюдать» и ограничения действий только записью в прод.

СИСТЕМНЫЕ ОТКАЗЫ. Группы с категорией отбора «системный отказ» — это стабильный ежедневный отказ функции (например ровно 5 падений в сутки много дней подряд, у одних и тех же аккаунтов). Объём мал, но повторяемость — основание проверить актуальное восстановление и тех же аккаунтов (для плановых синков действуют специальные правила выше): разбирай их так же серьёзно, как крупные инциденты, и при подтверждённой продолжающейся проблеме в дайджесте давай отдельный пункт с явной формулировкой «повторяется каждый день, N/сутки».

ВЕРДИКТЫ. Одна первопричина у нескольких групп → одинаковый action с пометкой «один инцидент с #<ID главной группы>». Мусорные обрезки (обрывки с {{}}[] посреди шаблона, счётчики 0–2) → «игнорировать», cause «обрезки сообщений», без расследования. Группы «DATA: Unknown transaction type» — всегда «действовать»: достань из примеров КОНКРЕТНЫЕ названия типов (operation_type / supplier_oper_name / bonus_type_name) и количество логов по каждому за сутки.

ДАЙДЖЕСТ — строго по шаблону (человеческий рассказ, НЕ список групп):

🧾 <ДД.ММ> — что было важного
Окно: <ЧЧ:ММ ДД.ММ> — <ЧЧ:ММ ДД.ММ> МСК (последние 24 часа), отчёт составлен в <ЧЧ:ММ>.

За окно <N> событий (по нашим группам, НЕ количество строк прод-лога), отказов джоб <M>. Главное: <одна фраза — самое важное и почему срочно>.

Таблица классов отказов — моноширинным блоком в тройных обратных кавычках (в Telegram нет markdown-таблиц, колонки выравнивать пробелами), топ-8 + «прочее»; строки с нулём показывать, если класс исчез после релиза. Колонки: Класс отказа, 24ч, Доля, Тренд 4д, Статус. Пример строки: CalculateAccountDayJob 14 74% 12·19·12·14 ▲ действовать.

1️⃣ <2–4 фразы: что случилось, причинная цепочка, масштаб. Обязательна строка эффекта (задето аккаунтов: N / подтверждённое восстановление, потерь нет / радиус не измерен с фактом результата SQL по правилу 7) и окно у любых счётчиков. Технические сущности в `моноширинном` виде. Всё выясненное чтением — здесь, в теле пункта.>
→ <Действие одной фразой — запись в прод/деплой/правка кода/участие человека; для непроверенного активного MONITORING — «проверить по точному контексту».> [#<ID> · строка <row>]({base}<row>)

(не более 4 пунктов; порядок следует из таблицы классов. В каждом: «Видно / Возможная причина / Проверить» и критерий проверки в действии. Нумерация ТОЛЬКО эмодзи 1️⃣2️⃣3️⃣…)

👀 Наблюдаем: <не более 3 активных групп с вердиктом «понаблюдать» и событиями за текущее окно; для каждой — простая суть, число за 24ч с источником, фактическое состояние восстановления или чего пока не удалось подтвердить, критерий эскалации и [#<ID> · строка <row>]({base}<row>). Для плановых синков: единичные 5xx/таймауты, планировщик повторит через 1–6ч; при наличии проверки — время last_success_at после отказа. Не включать сюда затихшие группы и не дублировать нумерованные пункты. Если активных наблюдений нет — «👀 Наблюдаем: активных групп нет.»>

🔁 Висит без изменений: <одна строка на пункт: «<суть> — <N> дней, статус не менялся». Сюда выносятся группы с вердиктом «действовать», которые повторяются день за днём без изменения статуса: за сутки событий нет или ничего нового не выяснилось. Число дней = сегодня минус дата из колонки «Впервые в действовать» (передана в списке). Такой пункт НЕ занимает нумерованное место; возвращать его в нумерованные пункты только при изменении статуса или всплеске.>

💸 Неучтённые операции за сутки: <НазваниеТипа1> — <N> шт, <НазваниеТипа2> — <N> шт (ТОЛЬКО название типа и количество за сутки: без сумм, без аккаунтов, без сравнений с прошлыми днями и без истории. Если DATA-логов за сутки не было — пункт ВСЁ РАВНО оставить одной строкой: «💸 Неучтённые операции: за сутки не было.») [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={UNKNOWN_TX_GID})

Со вчера: <что починилось (с фактом), что не повторилось, что рецидивировало.>

Фон: ~<X> логов известного шума (вердикт «игнорировать»). Не оценено: <N>.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={GROUPS_GID})

Текст СТРОГО ≤ 3400 символов, включая таблицу: хвост («Со вчера», «Фон», ссылка на таблицу) обязан уместиться целиком. Если не влезает — сокращай таблицу до топ-6 и пункты до трёх, а не обрывай текст. Пустой день: «Ничего требующего внимания: только известный фон (X логов)» + блок «Со вчера». В дайджест включай и вчерашние ещё актуальные инциденты со срочностью «сегодня» (их видно в контексте), не только сегодняшние группы."""

def recurring_issue_block(rows, today, sheet_id, text, verdicts):
    if re.search(r'^\s*🔁\s*Висит без изменений\s*:\s*\S', text, re.MULTILINE):
        return ''

    col = sheet_cols(rows)
    candidates = []
    urgency_order = {'сегодня': 0, 'неделя': 1, 'бэклог': 2}
    for index, row in enumerate(rows[1:], start=2):
        if col(row, 'Вердикт') != 'действовать' or verdicts.get(index, 'действовать') != 'действовать':
            continue
        try:
            age = (datetime.fromisoformat(today) - datetime.fromisoformat(col(row, 'Впервые в действовать'))).days
            daily, monthly = (int(col(row, name) or 0) for name in ('За 1 день', 'За 30 дней'))
        except ValueError:
            continue
        if age <= 0 or monthly <= 0 or (daily >= 10 and daily >= 3 * max(monthly / 30, 1)):
            continue
        issue_id = col(row, 'ID')
        if not re.fullmatch(r'[a-fA-F0-9]{8}', issue_id) or f'range=A{index})' in text or f'#{issue_id}' in text:
            continue
        # Для резервного блока используем нормализованный шаблон, без сырого лога.
        label = col(row, 'Ошибка (шаблон)')
        label = re.sub(r'^\w+\.[A-Z]+:\s*', '', label)
        label = re.sub(r'[{}\[\]`*_<>]', ' ', label)
        label = ' '.join(label.split())[:95]
        if not label:
            continue
        priority = urgency_order.get(col(row, 'Срочность'), 3)
        link = f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={GROUPS_GID}&range=A{index}'
        activity = f'{daily} событий за сутки' if daily else 'за сутки событий не было'
        line = f'• {label} — статус с {col(row, "Впервые в действовать")}, {activity}. [Группа {issue_id}]({link})'
        candidates.append((priority, -daily, index, line))

    if not candidates:
        return ''
    lines = [item[3] for item in sorted(candidates)[:3]]
    return '🔁 Висит без изменений:\n' + '\n'.join(lines)


def main():
    config = load_config()
    if not config.get('openai_api_key'):
        logging.error('openai_api_key не задан в config.json — агент не запущен.')
        return

    # Тестовый режим (сравнение моделей): ничего не пишем в таблицу,
    # вердикты и дайджест — в файлы logs/, идемпотентность отключена.
    test_mode = bool(os.environ.get('TRIAGE_TEST'))
    force = bool(os.environ.get('TRIAGE_FORCE'))
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
        if not test_mode and not no_digest and not force and (digest_ws.acell('A1').value or '').strip() == today:
            logging.info('Дайджест за %s уже записан — выходим.', today)
            return
        if force:
            logging.warning('[FIX:triage-force] Повторный триаж за %s запущен по явному запросу.', today)
    except gspread.exceptions.WorksheetNotFound:
        digest_ws = None if test_mode or no_digest else ss.add_worksheet(title='Digest', rows='10', cols='2')

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
    window_now = datetime.now(timezone.utc)
    unknown_groups, _ = aggregate_unknown_transactions(raw_rows[1:])
    unknown_summary = format_unknown_tx_summary(unknown_groups)
    unknown_line = (unknown_summary +
                    f' [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/{config["google_sheet_id"]}/edit#gid={UNKNOWN_TX_GID})')
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
        f"Точный счётчик Unknown tx из Original data за последние 24 часа (UTC): {unknown_summary}\n"
        f"Всего логов за сутки: {total_1d}, из них известный фон: {bg_1d}.\n"
        f"Групп на разбор: {len(worklist)} (бэклог: {n_backlog}, всплески: {n_spikes}, системные отказы: {n_systemic}).\n\n"
        "СПИСОК ГРУПП (row | id | за 1д/7д/30д | категория отбора | статус | старый вердикт | шаблон):\n" +
        '\n'.join(f"{it['row']} | #{it['id']} | {it['d1']}/{it['d7']}/{it['d30']} | {it['kind']} | "
                  f"{it['status']} | {it['verdict'] or '—'} | {it['pattern']}" for it in worklist) +
        "\n\nАКТУАЛЬНЫЕ «действовать» с активностью (контекст для дайджеста и связывания):\n" +
        ('\n'.join(acting) or '(нет)') +
        "\n\nАКТИВНЫЕ НАБЛЮДЕНИЯ (снимок Groups; обнови с учётом новых вердиктов):\n" +
        (watching_block(rows, config['google_sheet_id']) or '(нет)') +
        "\n\nРазбери группы и запиши вердикты, затем один раз вызови write_digest."
    )

    input_list = [{"role": "developer", "content": system_prompt(today, config['google_sheet_id'])},
                  {"role": "user", "content": user_msg}]

    verdicts_written = 0
    digest_written = False
    production_unavailable = False
    test_verdicts = []
    current_verdicts = {}
    current_actions = {}
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
                    result = get_examples(raw_cache, item['pattern'], item.get('monitoring', False))
            elif name in ('prod_time', 'prod_version', 'prod_grep', 'prod_read', 'prod_sql'):
                result = prod_gate(config, name.removeprefix('prod_'), **args)
                production_unavailable = production_unavailable or production_access_failed(result)
            elif name == 'write_verdict':
                row = int(args['row'])
                item = worklist_by_row.get(row)
                needs_investigation = (
                    item is not None and item['d1'] > 0
                    and (item.get('monitoring') or args.get('cause') != 'обрезки сообщений')
                    and row not in investigated
                )
                monitoring_check = None
                if not production_unavailable and not needs_investigation:
                    monitoring_check = monitoring_downgrade_check(
                        config, item, args['verdict'], raw_cache, window_now)
                    production_unavailable = production_unavailable or bool(
                        monitoring_check and monitoring_check.get('production_unavailable'))
                if item is None:
                    result = 'ОТКЛОНЕНО: строки нет в рабочем списке этого прогона.'
                elif production_unavailable and item['d1'] > 0:
                    result = ('ОТКЛОНЕНО: production недоступен, поэтому нельзя записывать '
                              'вердикт активной группы. Заверши прогон write_digest.')
                elif needs_investigation:
                    result = ('ОТКЛОНЕНО: активная группа без расследования. Сначала '
                              'вызови get_examples для её шаблона (и prod_grep для '
                              'production.ERROR), затем повтори write_verdict.')
                elif monitoring_check is not None and not monitoring_check['verified']:
                    result = ('ОТКЛОНЕНО: MONITORING нельзя понижать без подтверждённого восстановления по тому же контексту. '
                              + monitoring_check['reason'] + ' Запиши «действовать» с действием «проверить по точному контексту»; не утверждай подтверждённые потери.')
                else:
                    if test_mode:
                        test_verdicts.append(args)
                    else:
                        groups_ws.batch_update([{
                            'range': f'H{row}:M{row}',
                            'values': [['обработано', args['verdict'], args.get('urgency', ''),
                                        args['cause'], args['action'][:900], today]]}])
                    current_verdicts[row] = args['verdict']
                    current_actions[row] = args['action']
                    verdicts_written = len(current_verdicts)
                    result = f'ok, вердикт записан в строку {row}'
            elif name == 'write_digest':
                text = (args.get('text') or '').strip()
                budget_exceeded = usage['prompt'] > MAX_PROMPT_BUDGET
                if production_unavailable:
                    text = build_degraded_digest(today, total_1d, bg_1d, len(worklist))
                    logging.warning('[FIX:prod-access] Production недоступен; записан деградированный дайджест.')
                else:
                    recurring = recurring_issue_block(rows, today, config['google_sheet_id'], text, current_verdicts)
                    if recurring:
                        if '💸' in text:
                            text = text.replace('💸', recurring + '\n\n💸', 1)
                        else:
                            text += '\n\n' + recurring
                    text = attach_watching_block(text, watching_block(
                        rows, config['google_sheet_id'], current_verdicts, current_actions))
                # Счётчики операций считаем по датам, независимо от примеров модели.
                if '💸' in text:
                    text = re.sub(r'^.*💸.*$', lambda _: unknown_line, text, flags=re.MULTILINE)
                else:
                    text += '\n\n' + unknown_line
                if len(text) < 200:
                    result = 'ОТКЛОНЕНО: дайджест подозрительно короткий, напиши полноценный.'
                elif len(text) > 3950:
                    result = (f'ОТКЛОНЕНО: после обязательных блоков {len(text)} символов (максимум 3950). '
                              'Сократи рассказ и таблицу; блоки 👀, 💸 и хвост должны остаться целиком.')
                elif verdicts_written < len(worklist) and not budget_exceeded and not production_unavailable:
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
