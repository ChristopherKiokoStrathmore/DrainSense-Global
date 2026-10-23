"""manage.py set_backend_config: where APEX reaches the backend (NGAO_BACKEND_CONFIG.BASE_URL)."""
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase


class SetBackendConfigTests(TestCase):
    def run_command(self, url):
        out = StringIO()
        call_command("set_backend_config", base_url=url, stdout=out)
        return out.getvalue()

    def base_url(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT config_value FROM ngao_backend_config WHERE config_key = 'BASE_URL'")
            return [row[0] for row in cursor.fetchall()]

    def test_requires_https(self):
        with self.assertRaises(CommandError):
            self.run_command("http://ngao.example/ngao/v1")

    def test_skips_without_the_table(self):
        self.assertIn("not found", self.run_command("https://ngao.example/ngao/v1"))

    def test_inserts_then_updates(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE ngao_backend_config (config_key VARCHAR(40) PRIMARY KEY, "
                           "config_value VARCHAR(1000), updated_at TIMESTAMP)")
        self.run_command("https://1.2.3.4.sslip.io/ngao/v1/")
        self.assertEqual(self.base_url(), ["https://1.2.3.4.sslip.io/ngao/v1"])
        self.run_command("https://ngao.example/ngao/v1")
        self.assertEqual(self.base_url(), ["https://ngao.example/ngao/v1"])
