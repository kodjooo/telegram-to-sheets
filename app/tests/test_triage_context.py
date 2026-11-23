import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from triage_context import format_examples, is_monitoring, monitoring_contexts, watching_block

NOW = datetime(2026, 11, 23, 9, tzinfo=timezone.utc)


class MonitoringContextTests(unittest.TestCase):
    def test_monitoring_detection_case_insensitive(self):
        self.assertTrue(is_monitoring('production.ERROR: Monitoring: failed'))
        self.assertTrue(is_monitoring('other', 'MONITORING'))
        self.assertFalse(is_monitoring('other'))

    def test_full_json_after_900_and_distinct_pairs(self):
        samples = []
        for sid, source in [(81, 'wb'), (82, 'wb'), (81, 'ozon')]:
            text = 'production.ERROR: MONITORING: ' + 'x' * 1000 + json.dumps({
                'context': {'data_source': source, 'detail': {'statistics_id': sid, 'sendable': False, 'sent': False}}})
            samples.append(('2026-11-23 08:00:00', text))
        contexts, unresolved = monitoring_contexts(samples, NOW)
        self.assertEqual(unresolved, 0)
        self.assertEqual({(c['statistics_id'], c['data_source']) for c in contexts},
                         {(81, 'wb'), (82, 'wb'), (81, 'ozon')})
        text = format_examples(samples, monitoring=True)
        self.assertIn('"statistics_id":82', text)
        self.assertIn('"data_source":"ozon"', text)
        self.assertIn('"sendable":false', text)

    def test_window_is_utc_inclusive_and_excludes_future(self):
        text = '{"statistics_id":81,"data_source":"wb"}'
        samples = [(date, text) for date in ['2026-11-22 08:59:59', '2026-11-22 09:00:00',
                                           '2026-11-23 09:00:00', '2026-11-23 09:00:01']]
        contexts, unresolved = monitoring_contexts(samples, NOW)
        self.assertEqual(len(contexts), 2)
        self.assertEqual(unresolved, 0)
        with self.assertRaises(ValueError):
            monitoring_contexts(samples, NOW.replace(tzinfo=None))

    def test_missing_context_invalid_date_and_sibling_pairs_fail_closed(self):
        contexts, unresolved = monitoring_contexts([
            ('2026-11-23 08:00:00', '{"a":{"statistics_id":81},"b":{"data_source":"wb"}}'),
            ('invalid', '{}'), ('2026-11-23 08:01:00', 'broken {')], NOW)
        self.assertEqual(contexts, [])
        self.assertEqual(unresolved, 3)

    def test_mixed_valid_and_invalid_context_is_unresolved(self):
        text = '{"items":[{"statistics_id":81,"data_source":"wb"},{"statistics_id":82}]}'
        contexts, unresolved = monitoring_contexts([('2026-11-23 08:00:00', text)], NOW)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(unresolved, 1)

    def test_broken_outer_object_does_not_certify_valid_inner_context(self):
        text = 'production.ERROR: MONITORING: {"items":[{"statistics_id":81,"data_source":"wb"}]'
        contexts, unresolved = monitoring_contexts([('2026-11-23 08:00:00', text)], NOW)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(unresolved, 1)

    def test_repeated_observations_are_not_discarded_by_guard_input(self):
        text = '{"statistics_id":81,"data_source":"wb","sent":false}'
        contexts, unresolved = monitoring_contexts([('2026-11-23 08:00:00', text)] * 2, NOW)
        self.assertEqual(len(contexts), 2)
        self.assertEqual(unresolved, 0)
        self.assertIn('Различных пар statistics_id/data_source: 1', format_examples(
            [('2026-11-23 08:00:00', text)] * 2, monitoring=True))

    def test_examples_sort_by_actual_timestamp_with_offsets(self):
        samples = [('2026-11-23T10:00:00+03:00', 'older'),
                   ('2026-11-23T08:00:00+00:00', 'newer')]
        self.assertLess(format_examples(samples).index('newer'), format_examples(samples).index('older'))

    def test_non_monitoring_preserves_prefix_budget(self):
        text = format_examples([('2026-11-23', 'x' * 1200)])
        self.assertEqual(text.count('x'), 900)
        self.assertLessEqual(len(format_examples([('date', 'x' * 1200)], maxchars=100)), 100)


class WatchingBlockTests(unittest.TestCase):
    def test_current_overrides_active_only_and_three_limit(self):
        rows = [['ID', 'За 1 день', 'Вердикт', 'Действие', 'Ошибка (шаблон)']]
        rows += [[str(i), str(i), 'понаблюдать', f'action {i}', 'production.ERROR: MONITORING: расчёт задержан {} []'] for i in range(1, 6)]
        rows += [['zero', '0', 'понаблюдать', 'inactive', 'raw']]
        block = watching_block(rows, 'sheet', {
            6: {'verdict': 'игнорировать'},
            2: {'verdict': 'понаблюдать', 'action': 'current action'},
            3: {'verdict': 'действовать'},
        })
        self.assertEqual(block.count('• '), 3)
        self.assertIn('current action', block)
        self.assertNotIn('action 5', block)
        self.assertNotIn('action 2', block)
        self.assertNotIn('inactive', block)
        self.assertIn('MONITORING: расчёт задержан', block)
        self.assertNotIn('production.ERROR', block)
        self.assertNotIn('{}', block)
        self.assertIn('/24ч (Groups)', block)
        self.assertIn('range=A5', block)

    def test_no_active_watch_means_no_block(self):
        self.assertEqual(watching_block([['За 1 день', 'Вердикт'], ['0', 'понаблюдать']], 'sheet'), '')


if __name__ == '__main__':
    unittest.main()
