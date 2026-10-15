"""Pure, UTC-based aggregation for the rolling Unknown tx worksheet."""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone


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



def aggregate_unknown_transactions(rows, *, now=None):
    """Return (groups, malformed_ids) for the inclusive last 24 hours.

    Original data stores UTC timestamps without an offset. An explicit aware
    ``now`` makes the window reproducible and independent of the host timezone.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(hours=24)
    groups = defaultdict(lambda: {'1d': 0, '30d': 0, 'amount': 0.0,
                                  'first': None, 'last': None})
    malformed_ids = []
    for row in rows:
        if len(row) < 3:
            continue
        try:
            log_time = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if not cutoff <= log_time <= now:
            continue
        text = row[2]
        if 'Unknown transaction type' not in text:
            continue
        supplier = extract_from_json('supplier_oper_name', text)
        doc_type = extract_from_json('doc_type_name', text).removeprefix('/')
        operation_type = extract_from_json('operation_type', text)
        operation_type_name = extract_from_json('operation_type_name', text)
        payment_processing = extract_from_json('payment_processing', text)
        bonus_type = extract_from_json('bonus_type_name', text)
        platform = 'ВБ' if supplier else 'ОЗОН' if operation_type else '—'
        if platform == '—' and not doc_type and not payment_processing and not bonus_type:
            platform = 'Некорректный лог'
        key = (platform, operation_type or '—', doc_type or '—',
               operation_type_name or '—', supplier or '—',
               payment_processing or '—', bonus_type or '—')
        groups[key]['1d'] += 1
        try:
            groups[key]['amount'] += float(extract_from_json('amount', text).replace(',', '.'))
        except (TypeError, ValueError):
            pass
        if platform == 'Некорректный лог':
            malformed_ids.append(row[0])
    return dict(groups), malformed_ids


def format_unknown_tx_summary(groups):
    """Render the authoritative daily digest line from current window groups."""
    counts = defaultdict(int)
    for key, stats in groups.items():
        count = stats['1d']
        if count <= 0:
            continue
        label = next((key[i] for i in (1, 4, 6, 2) if key[i] != '—'), key[0])
        counts[label] += count
    if not counts:
        return '💸 Неучтённые операции: за сутки не было.'
    details = ', '.join(f'{label} — {count}' for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return f'💸 Неучтённые операции за сутки: {details}.'
