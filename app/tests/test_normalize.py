"""Тесты нормализации на реальных примерах логов.

Запуск: python3 -m unittest app.tests.test_normalize  (из корня репо)
или:    cd app && python3 -m unittest tests.test_normalize
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from normalize import is_fragment, merge_fragment_chains, normalize_error_pattern  # noqa: E402


class TestMerging(unittest.TestCase):
    """Разные экземпляры одной ошибки должны давать один шаблон."""

    def assert_same(self, a: str, b: str):
        na, nb = normalize_error_pattern(a), normalize_error_pattern(b)
        self.assertEqual(na, nb, f'\nНе склеилось:\n  {na}\n  {nb}')

    def test_ids(self):
        self.assert_same(
            'production.ERROR: SYNC: Order 123456 failed for account 9981',
            'production.ERROR: SYNC: Order 654321 failed for account 1002',
        )

    def test_datetimes(self):
        self.assert_same(
            'Syncing for more than 2026-07-26 03:40:11',
            'Syncing for more than 2026-07-27T09:15:00.317859+03:00',
        )

    def test_emails_uuids(self):
        self.assert_same(
            'Partner not found for user@mail.ru id a1b2c3d4-0000-4111-8222-333344445555',
            'Partner not found for other@gmail.com id ffffffff-9999-4888-b777-666655554444',
        )

    def test_json_payload(self):
        self.assert_same(
            'Ozon API response error for account: {"code":7,"data":{"id":123}}',
            'Ozon API response error for account: {"code":9}',
        )

    def test_word_number_tokens(self):
        self.assert_same(
            'Failed to download image vol1234/part56789.jpg',
            'Failed to download image vol9/part1.jpg',
        )

    def test_php_line_numbers(self):
        self.assert_same(
            'at app/Jobs/SyncJob.php:120',
            'at app/Jobs/SyncJob.php:987',
        )

    def test_floats_and_sums(self):
        self.assert_same(
            'Subscription turnover is higher than calculated for user 42: 1500.55',
            'Subscription turnover is higher than calculated for user 7: 99.01',
        )


class TestDiscrimination(unittest.TestCase):
    """Смысловые различия должны сохраняться — разные шаблоны."""

    def assert_diff(self, a: str, b: str):
        na, nb = normalize_error_pattern(a), normalize_error_pattern(b)
        self.assertNotEqual(na, nb, f'\nСклеилось избыточно: {na}')

    def test_sqlstate_codes(self):
        self.assert_diff(
            "SQLSTATE[23000]: Integrity constraint violation",
            "SQLSTATE[42S02]: Base table or view not found",
        )

    def test_curl_error_codes(self):
        self.assert_diff('cURL error 28: Operation timed out', 'cURL error 6: Could not resolve host')

    def test_http_status(self):
        self.assert_diff('Ozon API error code 502', 'Ozon API error code 403')

    def test_class_names(self):
        self.assert_diff(
            'Job App\\Jobs\\SyncOrdersJob failed',
            'Job App\\Jobs\\SyncStocksJob failed',
        )

    def test_file_paths(self):
        self.assert_diff(
            'at app/Jobs/SyncOrdersJob.php:10',
            'at app/Services/PaymentService.php:10',
        )

    def test_message_text(self):
        self.assert_diff('Account is blocked', 'Account is deleted')

    def test_exception_class_inside_json(self):
        self.assert_diff(
            'production.ERROR: EAFR batch failed {"account_id":8162,"error":"App\\Exceptions\\ServerProviderException | ..."}',
            'production.ERROR: EAFR batch failed {"account_id":8162,"error":"App\\Exceptions\\RateLimitException | ..."}',
        )


class TestSqlTails(unittest.TestCase):
    def test_same_error_different_sql(self):
        a = "SQLSTATE[HY000] [1040] Too many connections (Connection: mysql, Host: localhost, Port: 3306, Database: appsellerdata, SQL: SELECT COUNT(*) FROM a)"
        b = "SQLSTATE[HY000] [1040] Too many connections (Connection: mysql, Host: localhost, Port: 3306, Database: appsellerdata, SQL: insert into `failed_jobs` (`uuid`) values (1))"
        self.assertEqual(normalize_error_pattern(a), normalize_error_pattern(b))

    def test_different_column_not_merged(self):
        a = "SQLSTATE[42S22]: Column not found: 1054 Unknown column 'delivery_to_customer' in 'SET' (Connection: mysql, SQL: update `orders`)"
        b = "SQLSTATE[42S22]: Column not found: 1054 Unknown column 'other_column' in 'SET' (Connection: mysql, SQL: update `orders`)"
        self.assertNotEqual(normalize_error_pattern(a), normalize_error_pattern(b))

    def test_sqlstate_inside_json_kept(self):
        a = 'CalculateAccountDayJob failed {"account_id":1,"error":"SQLSTATE[42S22]: Column not found"}'
        b = 'CalculateAccountDayJob failed {"account_id":2,"error":"SQLSTATE[HY000]: General error"}'
        self.assertNotEqual(normalize_error_pattern(a), normalize_error_pattern(b))


class TestJunk(unittest.TestCase):
    def test_csv_dump_collapses(self):
        a = normalize_error_pattern('123;456;789;1011;1213;1415')
        b = normalize_error_pattern('9;8;7;6;5;4;3;2;1;0;99;98')
        self.assertEqual(a, b)

    def test_masked_emails(self):
        a = 'WB OAuth callback error: ключ принадлежит продавцу (Nadir*****@gmail.com)'
        b = 'WB OAuth callback error: ключ принадлежит продавцу (k.cond******@yandex.ru)'
        self.assertEqual(normalize_error_pattern(a), normalize_error_pattern(b))

    def test_random_tokens(self):
        a = 'Unable to create file laravel-excel-0h5D8HTf7FGzXo0sm4ol644slbJxfHTR.csv'
        b = 'Unable to create file laravel-excel-CEs48XBkEthiYwiJexCU1BL9sibwiG44.csv'
        self.assertEqual(normalize_error_pattern(a), normalize_error_pattern(b))


class TestMergingJsonClasses(unittest.TestCase):
    def test_same_class_different_ids(self):
        a = 'production.ERROR: EAFR batch failed {"account_id":8162,"jobs_count":17,"error":"App\\Exceptions\\ServerProviderException | msg"}'
        b = 'production.ERROR: EAFR batch failed {"account_id":4584,"jobs_count":11,"error":"App\\Exceptions\\ServerProviderException | msg"}'
        self.assertEqual(normalize_error_pattern(a), normalize_error_pattern(b))


class TestGuzzleStatus(unittest.TestCase):
    def test_http_status_kept(self):
        a = "production.ERROR: Client error: `GET https://x/y?id=1` resulted in a `403 Forbidden` response:"
        self.assertIn('403 Forbidden', normalize_error_pattern(a))

    def test_different_statuses_not_merged(self):
        a = "production.ERROR: Client error: `GET https://x/y?id=1` resulted in a `403 Forbidden` response:"
        b = "production.ERROR: Client error: `GET https://x/y?id=1` resulted in a `429 Too Many Requests` response:"
        self.assertNotEqual(normalize_error_pattern(a), normalize_error_pattern(b))


class TestTimestampPrefix(unittest.TestCase):
    def test_partial_timestamp_stripped(self):
        a = '26T14:59:20.317859 +03:00] production.ERROR: EAFR batch failed {"a":1}'
        b = 'production.ERROR: EAFR batch failed {"a":2}'
        self.assertEqual(normalize_error_pattern(a), normalize_error_pattern(b))

    def test_legit_text_not_stripped(self):
        a = 'production.WARNING: [1-retro] lock renew failed {}'
        self.assertTrue(normalize_error_pattern(a).startswith('production.WARNING:'))


class TestFragments(unittest.TestCase):
    def test_tails_are_fragments(self):
        for tail in [
            'ed {}[]', 'curr=rub', 'tail?appType=123',
            '240473003;240473006;240473009` resulted in a `403 Forbidden` response:',
            '"stored_rows":9} []',
        ]:
            self.assertTrue(is_fragment(tail), tail)

    def test_whole_messages_not_fragments(self):
        for head in [
            'production.ERROR: EAFR batch failed {"a":1}',
            '[2026-07-27T09:00:00+03:00] production.WARNING: Payment failed',
            'Problem: Load average is too high (per CPU load over 2.5)',
        ]:
            self.assertFalse(is_fragment(head), head)


class TestFragmentChains(unittest.TestCase):
    def test_chain_merged(self):
        logs = [
            {'id': 10, 'date': 'd', 'text': 'production.ERROR: Client error: `GET https://x?ids=1;2;'},
            {'id': 11, 'date': 'd', 'text': '3;4;5;6` resulted in a `498 '},
            {'id': 12, 'date': 'd', 'text': '` response:'},
            {'id': 13, 'date': 'd', 'text': 'production.WARNING: other {}'},
        ]
        merged = merge_fragment_chains(logs)
        self.assertEqual(len(merged), 2)
        self.assertIn('resulted in a `498 ` response:', merged[0]['text'])

    def test_orphan_dropped(self):
        logs = [
            {'id': 5, 'date': 'd', 'text': ';9;8` resulted in a `403 Forbidden` response:'},
            {'id': 20, 'date': 'd', 'text': 'production.ERROR: ok {}'},
        ]
        merged = merge_fragment_chains(logs)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]['text'].startswith('production.ERROR'))

    def test_glued_logs_resplit(self):
        logs = [
            {'id': 1, 'date': 'd', 'text': 'production.ERROR: Client error: `GET https://x?ids=1;2;'},
            {'id': 2, 'date': 'd', 'text': '3` resulted in a `403 Forbidden` response:[2026-07-27T09:00:00+03:00] production.WARNING: next log {}'},
        ]
        merged = merge_fragment_chains(logs)
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0]['text'].endswith('response:'))
        self.assertIn('next log', merged[1]['text'])

    def test_no_false_merge_on_gap(self):
        logs = [
            {'id': 1, 'date': 'd', 'text': 'production.ERROR: head {}'},
            {'id': 9, 'date': 'd', 'text': 'tail-like fragment without head'},
        ]
        merged = merge_fragment_chains(logs)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['text'], 'production.ERROR: head {}')


class TestStability(unittest.TestCase):
    def test_idempotent(self):
        raw = 'production.ERROR: SYNC: Order 123456 failed {"a":{"b":1}} at 2026-07-27 09:00:00'
        once = normalize_error_pattern(raw)
        self.assertEqual(once, normalize_error_pattern(once))

    def test_empty(self):
        self.assertEqual(normalize_error_pattern(''), '')
        self.assertEqual(normalize_error_pattern(None), '')


if __name__ == '__main__':
    unittest.main()
