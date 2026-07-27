"""
Нормализация текста лога в шаблон группы.

Принцип: маскируем ВЫСОКОКАРДИНАЛЬНЫЕ (случайные) части — ID, суммы, даты,
email, UUID, хеши, JSON-нагрузку; сохраняем НИЗКОКАРДИНАЛЬНЫЕ различительные
токены — коды ошибок (SQLSTATE[23000], cURL error 28, HTTP 404), имена
классов/исключений, пути файлов.

Применяется ко ВСЕМ логам (белый список SPECIAL_PATTERNS упразднён —
он существовал, чтобы GPT-шаг получал сырой текст; детали теперь берутся
из Original data).
"""

import re

# Плейсхолдер-защита: что нельзя маскировать, временно прячем.
_PROTECT_RE = [
    # SQLSTATE[23000], SQLSTATE[42S02] — код различает тип ошибки БД
    re.compile(r'SQLSTATE\[\w{1,6}\]'),
    # Явные коды ошибок вида "error 28", "code 502", "status 404", "HTTP 500"
    re.compile(r'\b(?:error|code|status|http)[ :=]{1,3}\d{1,3}\b', re.IGNORECASE),
    # Имена PHP-классов App\Jobs\SyncJob и т.п.
    re.compile(r'\b[A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+\b'),
    # HTTP-статус в Guzzle-ошибках: `403 Forbidden` response
    re.compile(r'`\s*\d{3}[^`\n]{0,40}`(?=\s*response)'),
]

_MASKS = [
    # Хвост Laravel-ошибок БД: всё после "(Connection:" — соединение и текст SQL.
    # Различительное (SQLSTATE-код, имя колонки/констрейнта) стоит ДО скобки.
    (re.compile(r'\(Connection:.*$', re.DOTALL), '(Connection: <sql>)'),
    # ISO/обычные datetime (с T или пробелом, с мс и таймзоной)
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2})?'), '<datetime>'),
    (re.compile(r'\b\d{4}-\d{2}-\d{2}\b'), '<date>'),
    (re.compile(r'\b\d{2}:\d{2}(?::\d{2})?\b'), '<time>'),
    # Email, в т.ч. частично скрытые звёздочками (Nadir*****@gmail.com)
    (re.compile(r'[\w.+*-]+@[\w*-]+\.[\w.*-]+'), '<email>'),
    (re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'), '<uuid>'),
    (re.compile(r'\b[a-f0-9]{16,64}\b'), '<hash>'),
    # Случайные токены: длинные смешанные буквенно-цифровые строки
    # (laravel-excel-0h5D8HTf7FGzXo0sm4ol…). Требуем и цифру, и букву.
    (re.compile(r'\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{20,}\b'), '<token>'),
    # Номер строки после .php: (меняется от деплоя к деплою)
    (re.compile(r'(\.php):\d+'), r'\1:<line>'),
    # Слово+число: vol1234, vol9, account_5678 → префикс сохраняем
    (re.compile(r'\b([A-Za-z_]{2,}?[_-]?)\d+\b'), r'\1<num>'),
    (re.compile(r'\b\d+\.\d+\b'), '<float>'),
    # Все оставшиеся числа — ID/суммы (смысловые коды спрятаны защитой выше)
    (re.compile(r'\b\d+\b'), '<num>'),
]

_JSON_INNER_RE = re.compile(r'\{[^{}]*\}')
_WS_RE = re.compile(r'\s+')

# Имена классов (App\Exceptions\Foo) — различительный признак, даже внутри JSON
_CLASS_RE = re.compile(r'\b[A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+\b')


def _mask_json(text: str) -> str:
    """Схлопывает JSON-объекты, включая вложенные, до {}."""
    prev = None
    # Изнутри наружу: {"a":{"b":1}} → {"a":<json>} → <json>; затем <json> → {}
    while prev != text:
        prev = text
        text = _JSON_INNER_RE.sub('\x01', text)
    text = re.sub(r'(\x01[,;\s]*)+', '\x01', text)
    return text.replace('\x01', '{}')


def normalize_error_pattern(text: str) -> str:
    if not text:
        return ''

    text = text.strip()

    # Обрывок timestamp-префикса перед "] " (артефакт старых шаблонов,
    # где скобка [ уже была срезана): "26T14:59:20.317 +03:00] production..."
    text = re.sub(r'^[\d<>a-z:.,+\-; T]{1,60}\]\s*', '', text)

    # 0. Запоминаем имена классов: если маскировка JSON их сотрёт,
    #    вернём их в конец шаблона — разные исключения не должны склеиваться.
    seen_classes = list(dict.fromkeys(_CLASS_RE.findall(text)))

    # 1. Прячем защищённые токены
    protected: list[str] = []

    def _stash(match: re.Match) -> str:
        protected.append(match.group(0))
        # Индекс кодируем буквами, чтобы его не съела маскировка чисел
        idx = len(protected) - 1
        letters = ''
        while True:
            letters = chr(ord('a') + idx % 26) + letters
            idx //= 26
            if idx == 0:
                break
        return f'\x00{letters}\x00'

    for rx in _PROTECT_RE:
        text = rx.sub(_stash, text)

    # 2. Маскируем случайные части
    text = _mask_json(text)
    for rx, repl in _MASKS:
        text = rx.sub(repl, text)

    # 3. Возвращаем защищённое на место
    restored: set[int] = set()

    def _unstash(match: re.Match) -> str:
        idx = 0
        for ch in match.group(1):
            idx = idx * 26 + (ord(ch) - ord('a'))
        restored.add(idx)
        return protected[idx]

    text = re.sub(r'\x00([a-z]+)\x00', _unstash, text)

    # Защищённые токены, стёртые маскировкой JSON (например SQLSTATE-код
    # внутри {"error": ...}), возвращаем в конец шаблона — это различители.
    lost_protected = [p for i, p in enumerate(protected) if i not in restored]

    # 3.5. Схлопываем повторяющиеся замаскированные последовательности
    # (CSV-дампы: <num>;<num>;<num>;… → <num>;…)
    text = re.sub(r'(?:<num>[;,\s|]+){2,}<num>', '<num>;…', text)
    text = re.sub(r'(?:<num>;…[;,\s|]*)+', '<num>;…', text)

    # 4. Возвращаем различители, потерянные при маскировке JSON
    lost = [c for c in seen_classes if c not in text]
    lost += [p for p in lost_protected if p not in text and p not in lost]
    if lost:
        text += ' [' + ', '.join(dict.fromkeys(lost)) + ']'

    # 4.5. Полностью замаскированный timestamp-префикс в начале — убираем
    text = re.sub(r'^\[?\s*<datetime>[^\]]{0,20}\]\s*', '', text)

    # 5. Схлопываем пробелы
    return _WS_RE.sub(' ', text).strip()


# Заголовки, с которых начинается ЦЕЛОЕ сообщение лога.
# Всё прочее — обрезок длинного сообщения, разрезанного Telegram
# (лимит 4096 символов): хвост без начала, анализу не подлежит.
_HEAD_RE = re.compile(r'^\s*\[?\s*(production\.\w+|Problem:|Resolved)', re.IGNORECASE)

# Начало нового лога внутри склеенного потока: [2026-07-27T09:00:00...] production.
_LOG_START_RE = re.compile(r'(?=\[\d{4}-\d{2}-\d{2}[T ][^\]]{0,40}\]\s*production\.)')


def is_fragment(text: str) -> bool:
    """True, если текст — обрезок разрезанного сообщения, а не целый лог."""
    if not text:
        return True
    return _HEAD_RE.search(normalize_error_pattern(text)[:80]) is None


def merge_fragment_chains(logs: list[dict]) -> list[dict]:
    """
    Склеивает цепочки разрезанных сообщений.

    Telegram режет длинные логи на несколько сообщений: голова + хвосты.
    Хвост всегда идёт следующим id за головой (или предыдущим хвостом).
    На входе — [{'id', 'date', 'text'}, ...]; на выходе — то же,
    но фрагменты приклеены к своей голове, отдельных строк не образуют.
    Хвост-сирота (голова вне выборки) отбрасывается.
    """
    merged: list[dict] = []
    for log in sorted(logs, key=lambda x: x['id']):
        if is_fragment(log.get('text', '')):
            if merged and log['id'] - merged[-1]['id'] <= merged[-1].get('_parts', 1):
                merged[-1]['text'] += log['text']
                merged[-1]['_parts'] = merged[-1].get('_parts', 1) + 1
                continue
            # Сирота: головы нет — пропускаем
            continue
        merged.append(dict(log))

    # Куски режутся без учёта границ логов: внутри склеенной цепочки может
    # начаться СЛЕДУЮЩИЙ лог. Разрезаем обратно по маркеру начала лога.
    result: list[dict] = []
    for m in merged:
        m.pop('_parts', None)
        pieces = _LOG_START_RE.split(m['text'])
        pieces = [p for p in pieces if p and p.strip()]
        for piece in pieces:
            entry = dict(m)
            entry['text'] = piece
            result.append(entry)
    return result
