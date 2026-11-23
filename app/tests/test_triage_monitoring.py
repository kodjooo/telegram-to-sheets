import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from triage_monitoring import check_weekly_monitoring


class WeeklyMonitoringTests(unittest.TestCase):
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)

    def sample(self, sid=10, source='weekly-mail', sendable=4, sent=6, date=None):
        return (date or self.now, 'MONITORING: ' + json.dumps({
            'statistics_id': sid, 'data_source': source, 'sendable': sendable, 'sent': sent}))

    def envelope(self, sid=10, source='weekly-mail', total=10, sent=10, unfinished=0, **extra):
        result = {'ok': True, 'rows': [{'statistics_id': sid, 'data_source': source,
                   'total': total, 'sent': sent, 'unfinished': unfinished}],
                  'row_count': 1, 'truncated': False}
        result.update(extra)
        return json.dumps(result)

    def run_check(self, samples=None, response=None):
        calls = []
        def query(sql):
            calls.append(sql)
            return self.envelope() if response is None else response
        result = check_weekly_monitoring(samples or [self.sample()], self.now, query)
        return result, calls

    def test_exact_pair_recovered_query_is_select_and_fk_is_correct(self):
        result, calls = self.run_check()
        self.assertTrue(result['verified'])
        self.assertEqual(len(calls), 1)
        sql = calls[0]
        self.assertTrue(sql.startswith('SELECT '))
        self.assertIn('d.account_report_statistics_id = s.id', sql)
        self.assertIn("WHERE s.id = 10 AND s.data_source = 'weekly-mail'", sql)
        self.assertIn('GROUP BY s.id, s.data_source LIMIT 1', sql)
        for status in ('email_sent', 'not_subscribed', 'account_disabled'):
            self.assertIn(status, sql)
        self.assertIn('d.status IS NULL OR', sql)
        self.assertEqual(sql.count('CAST(SUM('), 2)
        self.assertEqual(sql.count('AS UNSIGNED)'), 2)
        self.assertIn('statistics_id=10 data_source=weekly-mail total=10 sent=10', result['reason'])

    def test_unrelated_40_cannot_recover_10(self):
        result, _ = self.run_check(response=self.envelope(sid=40, total=40, sent=40))
        self.assertFalse(result['verified'])
        self.assertIn('не соответствует точным', result['checks'][0]['reason'])

    def test_same_id_wrong_source_cannot_recover(self):
        result, _ = self.run_check(response=self.envelope(source='another-source'))
        self.assertFalse(result['verified'])

    def test_all_pairs_checked_even_if_first_fails(self):
        calls = []
        def query(sql):
            calls.append(sql)
            sid = int(re.search(r'WHERE s.id = ([0-9]+)', sql).group(1))
            return self.envelope(sid=sid, unfinished=1 if sid == 10 else 0, sent=9 if sid == 10 else 10)
        result = check_weekly_monitoring([self.sample(40), self.sample(10)], self.now, query)
        self.assertFalse(result['verified'])
        self.assertEqual(len(calls), 2)
        self.assertEqual([check['verified'] for check in result['checks']], [False, True])

    def test_maximum_observed_sendable_plus_sent_per_pair(self):
        samples = [self.sample(sendable=3, sent=2), self.sample(sendable=4, sent=6)]
        result, calls = self.run_check(samples, self.envelope(sent=9, total=10))
        self.assertFalse(result['verified'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['checks'][0]['required_sent'], 10)
        result, _ = self.run_check(samples, self.envelope(sent=10, total=10))
        self.assertTrue(result['verified'])

    def test_counts_are_not_combined_across_pairs(self):
        def query(sql):
            sid = int(re.search(r'WHERE s.id = ([0-9]+)', sql).group(1))
            return self.envelope(sid=sid, total=10 if sid == 10 else 40, sent=10 if sid == 10 else 40)
        result = check_weekly_monitoring([self.sample(10), self.sample(40, sendable=30, sent=10)], self.now, query)
        self.assertTrue(result['verified'])

    def test_unfinished_unknown_status_prevents_downgrade(self):
        for unfinished in (1, 4):
            with self.subTest(unfinished=unfinished):
                result, _ = self.run_check(response=self.envelope(total=14, sent=10, unfinished=unfinished))
                self.assertFalse(result['verified'])

    def test_terminal_rows_accepted_only_if_previously_ready_rows_delivered(self):
        result, _ = self.run_check(response=self.envelope(total=15, sent=10, unfinished=0))
        self.assertTrue(result['verified'])
        result, _ = self.run_check(response=self.envelope(total=15, sent=9, unfinished=0))
        self.assertFalse(result['verified'])

    def test_zero_data_does_not_prove_recovery(self):
        result, _ = self.run_check([self.sample(sendable=0, sent=0)], self.envelope(total=0, sent=0))
        self.assertFalse(result['verified'])

    def test_denied_truncated_invalid_envelopes_fail_closed(self):
        responses = ['not json', 'null', '{}', self.envelope(ok=False),
                     self.envelope(truncated=True), self.envelope(row_count=0),
                     self.envelope(row_count=True), self.envelope(rows=[]),
                     self.envelope(rows=[{}, {}]), self.envelope(truncated=None),
                     json.dumps({'ok': False, 'error': 'private exception credentials'})]
        for response in responses:
            with self.subTest(response=response):
                result, _ = self.run_check(response=response)
                self.assertFalse(result['verified'])
                self.assertNotIn('credentials', result['reason'])

    def test_missing_null_negative_boolean_and_string_production_counters_fail(self):
        for counter in ('total', 'sent', 'unfinished'):
            for value in (None, -1, True, '10x', '1.0', '-1', ' 10', '01', 1.0):
                envelope = json.loads(self.envelope())
                envelope['rows'][0][counter] = value
                result, _ = self.run_check(response=json.dumps(envelope))
                self.assertFalse(result['verified'], (counter, value))
            envelope = json.loads(self.envelope())
            del envelope['rows'][0][counter]
            result, _ = self.run_check(response=json.dumps(envelope))
            self.assertFalse(result['verified'])

    def test_canonical_numeric_result_strings_normalize(self):
        envelope = json.loads(self.envelope())
        row = envelope['rows'][0]
        for field in ('statistics_id', 'total', 'sent', 'unfinished'):
            row[field] = str(row[field])
        result, _ = self.run_check(response=json.dumps(envelope))
        self.assertTrue(result['verified'])
        self.assertEqual(result['checks'][0]['total'], 10)
        self.assertEqual(result['checks'][0]['unfinished'], 0)

    def test_noncanonical_result_id_rejected(self):
        for sid in (True, 10.0, '10x', '1.0', '-1', ' 10', '010'):
            with self.subTest(sid=sid):
                result, _ = self.run_check(response=self.envelope(sid=sid))
                self.assertFalse(result['verified'])

    def test_inconsistent_counters_fail(self):
        result, _ = self.run_check(response=self.envelope(total=9, sent=10))
        self.assertFalse(result['verified'])

    def test_query_exception_no_private_error_and_all_pairs_attempted(self):
        calls = []
        def query(sql):
            calls.append(sql)
            raise PermissionError('private credentials')
        result = check_weekly_monitoring([self.sample(10), self.sample(40)], self.now, query)
        self.assertFalse(result['verified'])
        self.assertEqual(len(calls), 2)
        self.assertNotIn('credentials', result['reason'])

    def test_unresolved_missing_context_prevents_queries(self):
        samples = [self.sample(), (self.now, 'MONITORING without JSON')]
        result, calls = self.run_check(samples)
        self.assertFalse(result['verified'])
        self.assertEqual(calls, [])

    def test_invalid_context_values_fail_before_queries(self):
        invalid = [{'sid': 0}, {'sid': True}, {'sid': '10'},
                   {'source': "weekly-mail' OR 1=1"}, {'source': ''},
                   {'source': 'a' * 81}, {'source': 'почта'}, {'source': 42},
                   {'sendable': None}, {'sendable': True}, {'sendable': -1},
                   {'sent': None}, {'sent': True}, {'sent': -1}, {'sent': '6'}]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result, calls = self.run_check([self.sample(**arguments)])
                self.assertFalse(result['verified'])
                self.assertEqual(calls, [])

    def test_missing_observation_counter_rejects(self):
        sample = (self.now, '{"statistics_id":10,"data_source":"weekly-mail","sent":6}')
        result, calls = self.run_check([sample])
        self.assertFalse(result['verified'])
        self.assertEqual(calls, [])

    def test_budget_limits_distinct_pairs_not_observations(self):
        result, calls = self.run_check([self.sample(sid=sid) for sid in range(1, 22)])
        self.assertFalse(result['verified'])
        self.assertEqual(calls, [])
        result, calls = self.run_check([self.sample()] * 30)
        self.assertTrue(result['verified'])
        self.assertEqual(len(calls), 1)

    def test_window_only_inclusive_boundaries_no_pairs_fails(self):
        old = self.sample(sid=40, date=self.now - timedelta(hours=24, seconds=1))
        future = self.sample(sid=40, date=self.now + timedelta(seconds=1))
        result, calls = self.run_check([old, future])
        self.assertFalse(result['verified'])
        self.assertEqual(calls, [])
        result, calls = self.run_check([old, future, self.sample(date=self.now - timedelta(hours=24))])
        self.assertTrue(result['verified'])
        self.assertEqual(len(calls), 1)

    def test_invalid_or_naive_time_fails_without_query(self):
        for now in (self.now.replace(tzinfo=None), None):
            calls = []
            result = check_weekly_monitoring([self.sample()], now, lambda sql: calls.append(sql))
            self.assertFalse(result['verified'])
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
