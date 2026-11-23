"""Pure context extraction and digest helpers; no credentials or network access."""

import json
import re
from datetime import datetime, timedelta, timezone

_FIELDS = ('data_source', 'statistics_id', 'sendable', 'sent')


def is_monitoring(pattern, category=''):
    return 'monitoring:' in pattern.lower() or 'monitoring' in category.lower()


def _date(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    # Original data timestamps without offsets are UTC.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _json_objects(text):
    decoder = json.JSONDecoder()
    offset = 0
    while offset < len(text):
        match = re.search(r'[\[{]', text[offset:])
        if match is None:
            return
        start = offset + match.start()
        try:
            obj, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            # A broken object can contain valid inner objects. Keep its failure
            # so those inner objects cannot falsely certify complete coverage.
            if text[start] == '{':
                yield None
            offset = start + 1
            continue
        yield obj
        offset = start + length


def _extract_contexts(text):
    found, incomplete = [], []

    def walk(obj, inherited=None):
        context = dict(inherited or {})
        if isinstance(obj, dict):
            context.update({key: obj[key] for key in _FIELDS if key in obj})
            sid, source = context.get('statistics_id'), context.get('data_source')
            valid_id = (not isinstance(sid, bool) and isinstance(sid, (str, int))
                        and str(sid).isdigit() and int(sid) > 0)
            valid_source = (not isinstance(source, bool) and isinstance(source, (str, int))
                            and bool(str(source).strip()))
            if valid_id and valid_source and any(key in obj for key in ('statistics_id', 'data_source')):
                found.append(context)
            before = len(found)
            for child in obj.values():
                if isinstance(child, (dict, list)):
                    walk(child, context)
            if (any(key in obj for key in ('statistics_id', 'data_source'))
                    and not (valid_id and valid_source) and len(found) == before):
                incomplete.append(context)
        elif isinstance(obj, list):
            for child in obj:
                walk(child, context)

    for obj in _json_objects(text):
        if obj is None:
            incomplete.append({})
        else:
            walk(obj)
    return found, incomplete


def _contexts(text):
    return _extract_contexts(text)[0]


def monitoring_contexts(samples, now):
    """Return (contexts, unresolved_count) for inclusive [now-24h, now].

    Samples are ``(date, full_text)`` pairs. Each returned dict contains its
    ``date`` (UTC ISO string), exact ``statistics_id``/``data_source`` values,
    and any sendable/sent fields. Repeated pairs are retained as observations.
    Unresolved counts samples with missing/invalid pairs or invalid timestamps;
    callers must fail closed when it is nonzero. Never pair sibling objects.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    now = now.astimezone(timezone.utc)
    contexts, unresolved = [], 0
    for date, text in samples:
        try:
            timestamp = _date(date)
        except (TypeError, ValueError):
            unresolved += 1
            continue
        if not now - timedelta(hours=24) <= timestamp <= now:
            continue
        extracted, incomplete = _extract_contexts(text)
        if not extracted or incomplete:
            unresolved += 1
        contexts.extend(dict(context, date=timestamp.isoformat()) for context in extracted)
    return contexts, unresolved


def format_examples(samples, monitoring=False, maxchars=6000):
    """Display at most three latest distinct pairs, extracting JSON before cutting.

    The display is illustrative, never the complete input to a downgrade guard.
    Non-monitoring examples retain the previous 900-character raw prefix.
    """
    if not samples:
        return '(сырых примеров за 30 дней не найдено — возможно, группа старая)'
    def sort_time(sample):
        try:
            return _date(sample[0])
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    ordered = sorted(samples, key=sort_time, reverse=True)
    selected, seen = [], set()
    for date, text in ordered:
        contexts = _contexts(text) if monitoring else []
        if monitoring:
            for context in contexts:
                identity = (str(context['statistics_id']), str(context['data_source']))
                if identity not in seen:
                    seen.add(identity)
                    selected.append((date, text, context))
        else:
            selected.append((date, text, None))
    if monitoring and not selected:
        selected = [(date, text, None) for date, text in ordered[:3]]
    blocks = []
    for date, text, context in selected[:3]:
        details = ('Контекст: ' + json.dumps(context, ensure_ascii=False, separators=(',', ':')) + '\n') if context else ''
        blocks.append(f'[{date}] {details}{text[:900]}')
    heading = (f'Различных пар statistics_id/data_source: {len(seen)}; '
               f'показано: {min(len(selected), 3)}. Это примеры, не полный охват.\n') if monitoring else ''
    return (heading + '\n---\n'.join(blocks))[:maxchars]


def watching_block(rows, sheet_id, verdicts=None, actions=None):
    """Build <=3 active watch entries from Sheets rows, applying row-number overrides.

    ``verdicts`` maps integer sheet row numbers to verdict strings or write_verdict
    argument dicts. ``actions`` optionally maps row numbers to action strings.
    Display a cleaned normalized subject, Groups volume, ID and current action.
    Raw example text and JSON payloads are never used here.
    """
    if not rows:
        return ''
    verdicts, actions = verdicts or {}, actions or {}
    indices = {name.strip(): index for index, name in enumerate(rows[0])}

    def cell(row, name):
        index = indices.get(name)
        return row[index].strip() if index is not None and index < len(row) else ''

    candidates = []
    for number, row in enumerate(rows[1:], 2):
        override = verdicts.get(number, {})
        override = {'verdict': override} if isinstance(override, str) else override
        verdict = override.get('verdict', cell(row, 'Вердикт'))
        count = cell(row, 'За 1 день')
        count = int(count) if count.isdigit() else 0
        if verdict != 'понаблюдать' or count <= 0:
            continue
        action = actions.get(number, override.get('action', cell(row, 'Действие')))
        action = re.sub(r'\s+', ' ', action).strip()[:100]
        subject = cell(row, 'Ошибка (шаблон)')
        subject = re.sub(r'^.*?production\.\w+:\s*', '', subject, flags=re.IGNORECASE)
        subject = re.sub(r'<[^>]*>|[{}\[\]]', ' ', subject)
        subject = re.sub(r'\s+', ' ', subject).strip(' :,-')[:80]
        identity = re.sub(r'[^\w-]', '', cell(row, 'ID')) or str(number)
        url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=1412410715&range=A{number}'
        candidates.append((count, number, f'• {subject + " — " if subject else ""}[#{identity}]({url}): {count}/24ч (Groups); {action or "наблюдение продолжается"}'))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return '👀 Наблюдаем:\n' + '\n'.join(item[2] for item in candidates[:3]) if candidates else ''
