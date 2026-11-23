"""Регрессии доступа production и безопасной деградации дайджеста."""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, APP_DIR)
os.environ.setdefault('APP_DIR', APP_DIR)
os.makedirs(os.path.join(APP_DIR, 'logs'), exist_ok=True)

# Проверяем чистые функции без установки интеграционных зависимостей локально.
sys.modules.setdefault('gspread', MagicMock())
oauth2client = types.ModuleType('oauth2client')
service_account = types.ModuleType('oauth2client.service_account')
service_account.ServiceAccountCredentials = MagicMock()
oauth2client.service_account = service_account
sys.modules.setdefault('oauth2client', oauth2client)
sys.modules.setdefault('oauth2client.service_account', service_account)
httpx = types.ModuleType('httpx')
httpx.Client = MagicMock()
sys.modules.setdefault('httpx', httpx)
openai = types.ModuleType('openai')
openai.OpenAI = MagicMock()
sys.modules.setdefault('openai', openai)

from triage_agent import (  # noqa: E402
    PROD_KNOWN_HOSTS_PATH,
    build_degraded_digest,
    production_access_failed,
)


class TestProductionAccess(unittest.TestCase):
    def test_host_key_error_switches_to_degraded_mode(self):
        self.assertTrue(production_access_failed('[stderr] Host key verification failed.'))

    def test_regular_query_error_is_not_transport_failure(self):
        self.assertFalse(production_access_failed('ERROR 1146: Table does not exist'))

    def test_degraded_digest_does_not_claim_an_incident(self):
        digest = build_degraded_digest('2026-09-25', 17, 5, 3)
        self.assertIn('не подтверждает инциденты', digest)
        self.assertIn('нельзя проверить', digest)
        self.assertNotIn('→', digest)

    def test_known_hosts_file_is_part_of_application(self):
        self.assertEqual(PROD_KNOWN_HOSTS_PATH, os.path.join(APP_DIR, 'prod_known_hosts'))


if __name__ == '__main__':
    unittest.main()
