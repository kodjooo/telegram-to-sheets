"""Fail-closed, read-only recovery checks for weekly-mail MONITORING signals."""

import json
import re

from triage_context import monitoring_contexts

_SOURCE = re.compile(r'[A-Za-z0-9_-]{1,80}', re.ASCII)
_MAX_CONTEXTS = 20


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _result_int(value):
    """Normalize native SQL integers and canonical decimal strings only."""
    if _nonnegative_int(value):
        return value
    if isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value, re.ASCII):
        try:
            return int(value)
        except ValueError:
            pass
    return None


def check_weekly_monitoring(samples, now, query_fn):
    """Verify every exact pair observed in the rolling 24-hour sample window.

    ``samples`` must contain complete texts, not displayed/truncated examples.
    ``query_fn(sql)`` returns the production gateway JSON envelope. This helper
    only issues SELECTs; any missing evidence keeps ``verified`` false.
    """
    checks = []

    def result(verified, reason):
        return {'verified': verified, 'reason': reason, 'checks': checks}

    try:
        contexts, unresolved = monitoring_contexts(samples, now)
    except (TypeError, ValueError, AttributeError):
        return result(False, 'Некорректные примеры или окно отчёта; восстановление не подтверждено.')
    if unresolved:
        return result(False, f'Примеры за 24ч без полного контекста: {unresolved}; восстановление не подтверждено.')
    if not contexts:
        return result(False, 'За 24ч нет точных пар statistics_id/data_source.')

    expected = {}
    for context in contexts:
        sid, source = context.get('statistics_id'), context.get('data_source')
        if type(sid) is not int or sid <= 0:
            return result(False, 'Некорректный statistics_id: требуется положительное целое число.')
        if not isinstance(source, str) or _SOURCE.fullmatch(source) is None:
            return result(False, 'Некорректный data_source: требуется точный источник в допустимом формате.')
        sendable, sent = context.get('sendable'), context.get('sent')
        if not _nonnegative_int(sendable) or not _nonnegative_int(sent):
            return result(False, 'В примерах отсутствуют или некорректны счётчики sendable/sent.')
        pair = (sid, source)
        expected[pair] = max(expected.get(pair, 0), sent + sendable)
    if len(expected) > _MAX_CONTEXTS:
        return result(False, f'Слишком много различных контекстов: {len(expected)}; лимит {_MAX_CONTEXTS}.')

    for (sid, source), required_sent in sorted(expected.items()):
        check = {'statistics_id': sid, 'data_source': source,
                 'required_sent': required_sent, 'verified': False}
        checks.append(check)
        # Source is restricted to ASCII letters/digits/underscore/hyphen above.
        # NULL and unknown statuses count as unfinished, never as recovery.
        sql = (
            'SELECT s.id AS statistics_id, s.data_source AS data_source, '
            'COUNT(d.id) AS total, '
            "CAST(SUM(CASE WHEN d.status = 'email_sent' THEN 1 ELSE 0 END) AS UNSIGNED) AS sent, "
            'CAST(SUM(CASE WHEN d.id IS NOT NULL AND (d.status IS NULL OR '
            "d.status NOT IN ('email_sent','not_subscribed','account_disabled')) "
            'THEN 1 ELSE 0 END) AS UNSIGNED) AS unfinished '
            'FROM account_report_statistics AS s '
            'LEFT JOIN account_report_statistics_data AS d '
            'ON d.account_report_statistics_id = s.id '
            f"WHERE s.id = {sid} AND s.data_source = '{source}' "
            'GROUP BY s.id, s.data_source LIMIT 1'
        )
        try:
            raw = query_fn(sql)
        except Exception:
            check['reason'] = 'Запрос к production недоступен или запрещён.'
            continue
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            check['reason'] = 'Некорректный JSON результата production.'
            continue
        if (not isinstance(envelope, dict) or envelope.get('ok') is not True
                or envelope.get('truncated') is not False
                or type(envelope.get('row_count')) is not int
                or envelope['row_count'] != 1
                or not isinstance(envelope.get('rows'), list)
                or len(envelope['rows']) != 1):
            check['reason'] = 'Результат production запрещён, неполон, обрезан или не содержит точной строки.'
            continue
        row = envelope['rows'][0]
        if (not isinstance(row, dict)
                or _result_int(row.get('statistics_id')) != sid or row.get('data_source') != source):
            check['reason'] = 'Строка production не соответствует точным statistics_id/data_source.'
            continue
        counters = {name: _result_int(row.get(name)) for name in ('total', 'sent', 'unfinished')}
        if not all(_nonnegative_int(value) for value in counters.values()):
            check['reason'] = 'Счётчики production отсутствуют, равны null или некорректны.'
            continue
        check.update(counters)
        if counters['sent'] + counters['unfinished'] > counters['total']:
            check['reason'] = 'Счётчики production противоречат друг другу.'
        elif counters['total'] == 0:
            check['reason'] = 'Нет строк данных отчёта; восстановление не подтверждено.'
        elif counters['unfinished']:
            check['reason'] = 'Остались незавершённые строки отчёта, включая неизвестные статусы.'
        elif counters['sent'] < required_sent:
            check['reason'] = 'Отправленных строк меньше ранее готовых к отправке и уже отправленных.'
        else:
            check['verified'] = True
            check['reason'] = 'Восстановление точного отчёта подтверждено; все ранее готовые строки отправлены.'

    verified = all(check['verified'] for check in checks)
    detail = '; '.join(
        f"statistics_id={check['statistics_id']} data_source={check['data_source']} "
        f"total={check.get('total', 'недоступно')} sent={check.get('sent', 'недоступно')} "
        f"unfinished={check.get('unfinished', 'недоступно')} required_sent={check['required_sent']}: "
        f"{check['reason']}" for check in checks
    )
    return result(verified, detail)
