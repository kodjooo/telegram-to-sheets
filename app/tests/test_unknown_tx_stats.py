import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unknown_tx_stats import aggregate_unknown_transactions, format_unknown_tx_summary


class UnknownTxStatsTests(unittest.TestCase):
    now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

    def row(self, identifier, date, payload='{"operation_type":"fee","amount":"12,50"}'):
        return [identifier, date, 'Unknown transaction type ' + payload]

    def test_inclusive_boundaries_exclude_old_and_future_rows_and_ids(self):
        rows = [
            self.row('old', '2026-10-14 09:59:59', '{}'),
            self.row('start', '2026-10-14 10:00:00', '{}'),
            self.row('end', '2026-10-15 10:00:00', '{}'),
            self.row('future', '2026-10-15 10:00:01', '{}'),
            self.row('obsolete-type', '2026-10-01 10:00:00'),
        ]
        groups, identifiers = aggregate_unknown_transactions(rows, now=self.now)
        self.assertEqual(identifiers, ['start', 'end'])
        self.assertEqual(len(groups), 1)
        self.assertEqual(next(iter(groups.values()))['1d'], 2)
        self.assertEqual(next(iter(groups))[0], 'Некорректный лог')

    def test_moscow_now_means_same_utc_window(self):
        rows = [self.row('included', '2026-10-14 11:00:00')]
        utc = aggregate_unknown_transactions(rows, now=self.now)
        moscow = aggregate_unknown_transactions(rows, now=self.now.astimezone(timezone(timedelta(hours=3))))
        self.assertEqual(utc, moscow)
        self.assertEqual(next(iter(utc[0].values()))['amount'], 12.5)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_unknown_transactions([], now=self.now.replace(tzinfo=None))

    def test_invalid_rows_and_non_unknown_messages_ignored(self):
        groups, identifiers = aggregate_unknown_transactions([
            [], ['id', 'bad', 'Unknown transaction type'],
            ['id', '2026-10-15 09:00:00', 'normal message'],
        ], now=self.now)
        self.assertEqual(groups, {})
        self.assertEqual(identifiers, [])
        self.assertEqual(format_unknown_tx_summary(groups), '💸 Неучтённые операции: за сутки не было.')

    def test_summary_uses_operation_then_supplier_and_aggregates(self):
        groups, _ = aggregate_unknown_transactions([
            self.row('1', '2026-10-15 09:00:00'),
            self.row('2', '2026-10-15 09:00:00'),
            self.row('3', '2026-10-15 09:00:00', '{"supplier_oper_name":"sale"}'),
        ], now=self.now)
        self.assertEqual(format_unknown_tx_summary(groups), '💸 Неучтённые операции за сутки: fee — 2, sale — 1.')


if __name__ == '__main__':
    unittest.main()
