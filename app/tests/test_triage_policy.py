"""Политика триажа: все внешние клиенты подменены, запись только в tmp."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
_dependencies = {name: MagicMock() for name in
                 ('gspread', 'httpx', 'oauth2client', 'oauth2client.service_account', 'openai')}
_spec = importlib.util.spec_from_file_location('triage_policy_under_test', APP_DIR / 'triage_agent.py')
agent = importlib.util.module_from_spec(_spec)
with patch.dict(sys.modules, _dependencies), patch('logging.basicConfig'):
    _spec.loader.exec_module(agent)

HEADER = ['ID', 'Ошибка (шаблон)', 'Категория', 'За 1 день', 'За 7 дней',
          'За 30 дней', 'Статус', 'Вердикт', 'Действие', 'Впервые в действовать']


def row(identity, pattern, count=1, status='обработано', verdict='действовать', category=''):
    return [str(identity), pattern, category, str(count), str(count * 7),
            str(count * 30), status, verdict, 'проверить по точному контексту', '2026-11-22']


class TriagePolicyTests(unittest.TestCase):
    def test_processed_active_monitoring_precedes_large_backlog(self):
        rows = [HEADER] + [row(i, 'production.ERROR: backlog ' + str(i), 500,
                                status='не обработано') for i in range(agent.MAX_GROUPS + 3)]
        rows += [row('active', 'MONITORING: [weekly-mail] report stalled', 1),
                 row('quiet', 'MONITORING: [weekly-mail] old report', 0)]
        work, *_ = agent.build_worklist(rows, weekday=2)
        self.assertEqual(len(work), agent.MAX_GROUPS)
        self.assertEqual(work[0]['id'], 'active')
        self.assertTrue(work[0]['monitoring'])
        self.assertIn('обязательная перепроверка', work[0]['kind'])
        self.assertNotIn('quiet', [item['id'] for item in work])

    def test_examples_extract_distinct_context_after_raw_prefix(self):
        pattern = 'MONITORING: [weekly-mail] stalled'
        samples = []
        for hour, sid, source in [(10, 11, 'wb'), (11, 12, 'ozon'), (12, 11, 'wb')]:
            payload = json.dumps(dict(statistics_id=sid, data_source=source, sendable=9, sent=2))
            samples.append((f'2026-11-23 {hour}:00:00', 'x' * 1100 + payload))
        result = agent.get_examples({pattern: samples}, pattern)
        self.assertIn('Различных пар statistics_id/data_source: 2', result)
        self.assertEqual(result.count('Контекст:'), 2)
        self.assertIn('"statistics_id":11', result)
        self.assertIn('"statistics_id":12', result)
        self.assertIn('"data_source":"ozon"', result)
        self.assertIn('"sendable":9', result)
        self.assertIn('"sent":2', result)

    def test_authoritative_watch_replaces_model_block_preserves_footer(self):
        footer = '🔁 Без изменений\n\n💸 Операции\n\nСо вчера: исправлено\n\nФон: 2\n\n👉 [Вся таблица](https://example.com)'
        text = 'Отчёт\n\n👀 Ошибочный список модели\n• выдуманный пункт\n\n' + footer
        block = '👀 Наблюдаем:\n• проверенное наблюдение'
        result = agent.attach_watching_block(text, block)
        self.assertEqual(result.count('👀'), 1)
        self.assertIn(block, result)
        self.assertNotIn('выдуманный', result)
        self.assertTrue(result.endswith(footer))
        self.assertEqual(agent.attach_watching_block(result, block), result)

    def test_downgrade_guard_only_checks_weekly_monitoring(self):
        now = agent.datetime.now(agent.timezone.utc)
        weekly = dict(monitoring=True, pattern='MONITORING: [weekly-mail] stalled')
        samples = [('2026-11-23 10:00:00', 'full context')]
        with patch.object(agent, 'check_weekly_monitoring', return_value={'verified': True}) as check, patch.object(agent, 'prod_gate', return_value='sql result') as gate:
            self.assertIsNone(agent.monitoring_downgrade_check({}, None, 'игнорировать', {}, now))
            self.assertIsNone(agent.monitoring_downgrade_check({}, dict(monitoring=False), 'игнорировать', {}, now))
            self.assertIsNone(agent.monitoring_downgrade_check({}, weekly, 'действовать', {}, now))
            check.assert_not_called()
            result = agent.monitoring_downgrade_check({'setting': 1}, weekly, 'понаблюдать', {weekly['pattern']: samples}, now)
            self.assertTrue(result['verified'])
            self.assertIs(check.call_args.args[0], samples)
            self.assertEqual(check.call_args.args[1], now)
            callback = check.call_args.args[2]
            self.assertEqual(callback('SELECT 1'), 'sql result')
            gate.assert_called_once_with({'setting': 1}, 'sql', query='SELECT 1')

    def test_nonweekly_monitoring_cannot_use_fragment_excuse_to_downgrade(self):
        item = dict(monitoring=True, pattern='MONITORING: [billing] missing', cause='обрезки сообщений')
        with patch.object(agent, 'check_weekly_monitoring') as check, patch.object(agent, 'prod_gate') as gate:
            for verdict in ('игнорировать', 'понаблюдать'):
                result = agent.monitoring_downgrade_check({}, item, verdict, {}, agent.datetime.now(agent.timezone.utc))
                self.assertFalse(result['verified'])
                self.assertIn('Сохрани «действовать»', result['reason'])
            check.assert_not_called()
            gate.assert_not_called()

    def test_runtime_prompt_has_safe_sql_and_explicit_sync_policy(self):
        prompt = agent.system_prompt('2026-11-23', 'sheet')
        self.assertIn('account_?[Ii]d...{1,4}i:[0-9]+', prompt)
        self.assertNotIn('{{1,4}}', prompt)
        self.assertNotIn('\x00', prompt)
        self.assertIn(r'\u0000', prompt)
        for job in ('SyncOrdersJob', 'TransactionOnlineJob', 'SyncTransactionsJob', 'SyncProductsJob', 'SyncWarehousesJob'):
            self.assertIn(job, prompt)
        self.assertIn('через 1–6 часов', prompt)
        self.assertIn('не предлагай retry failed_jobs, queue:retry или добавление ретраев', prompt)
        self.assertIn('account_sync_schedule.last_success_at', prompt)
        self.assertIn('ДО успешной проверки на production по точному контексту события статус — «действовать»', prompt)

    def test_main_testmode_rejects_monitoring_downgrade_and_writes_only_diagnostic(self):
        rows = [HEADER, row('signal', 'MONITORING: [billing] missing')]
        groups, digest, original = MagicMock(), MagicMock(), MagicMock()
        groups.get_all_values.return_value = rows
        original.get_all_values.return_value = [['ID', 'Дата', 'Текст']]
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = {'Groups': groups, 'Digest': digest, 'Original data': original}.__getitem__
        def call(name, **args):
            return SimpleNamespace(type='function_call', name=name, arguments=json.dumps(args), call_id=name + str(len(args)))
        common = dict(row=2, urgency='сегодня', cause='обрезки сообщений', action='проверить по точному контексту')
        calls = [call('get_examples', row=2), call('write_verdict', verdict='игнорировать', **common),
                 call('write_verdict', verdict='действовать', **common),
                 call('write_digest', text='Отчёт за последние 24 часа. ' * 12 + '\n\n👀 Модельный список\n\nФон: 0\n\n👉 Таблица')]
        response = SimpleNamespace(output=calls, usage=None)
        client = MagicMock()
        client.responses.create.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'logs').mkdir()
            environment = {'TRIAGE_TEST': '1', 'TRIAGE_MODEL': 'mock-model', 'TRIAGE_EFFORT': 'low'}
            with patch.dict(os.environ, environment, clear=True), patch.object(agent, 'BASE_DIR', directory), patch.object(agent, 'load_config', return_value={'openai_api_key': 'fake', 'google_sheet_id': 'sheet', 'openai_proxy': ''}), patch.object(agent, 'open_spreadsheet', return_value=spreadsheet), patch.object(agent, 'OpenAI', return_value=client), patch.object(agent, 'get_examples', return_value='raw example') as examples, patch.object(agent, 'monitoring_downgrade_check', side_effect=lambda config, item, verdict, cache, now: None if verdict == 'действовать' else {'verified': False, 'reason': 'контекст не проверен'}) as guard, patch.object(agent, 'prod_gate', side_effect=AssertionError('production forbidden')):
                agent.main()
            files = list(Path(directory).rglob('*'))
            diagnostic = Path(directory, 'logs', 'digest_test_mock-model_low.md')
            self.assertEqual([path for path in files if path.is_file()], [diagnostic])
            text = diagnostic.read_text()
            verdicts = json.loads(text.split('===ВЕРДИКТЫ===\n', 1)[1])
            self.assertEqual([value['verdict'] for value in verdicts], ['действовать'])
            examples.assert_called_once()
            self.assertEqual(guard.call_count, 2)
            outputs = [item['output'] for item in client.responses.create.call_args.kwargs['input']
                       if isinstance(item, dict) and item.get('type') == 'function_call_output']
            self.assertTrue(any('MONITORING нельзя понижать' in output for output in outputs))
            self.assertIn('ok, вердикт записан в строку 2', outputs)
            groups.batch_update.assert_not_called()
            digest.update.assert_not_called()
            original.batch_update.assert_not_called()
            spreadsheet.add_worksheet.assert_not_called()


if __name__ == '__main__':
    unittest.main()
