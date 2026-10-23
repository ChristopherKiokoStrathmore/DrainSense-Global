from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from ngao import demo_data

API_TOKEN = "test-api-token"
USSD_TOKEN = "test-ussd-token"

TEST_SETTINGS = dict(
    NGAO_API_TOKENS=[API_TOKEN],
    NGAO_USSD_SERVICE_TOKEN=USSD_TOKEN,
    NGAO_WEBHOOK_SECRET=None,
    NGAO_ASYNC_TASKS=False,
    NGAO_SMS_BACKEND="console",
    NGAO_SMS_DISPATCH="inline",
    NGAO_SMS_SIMULATE_SYNTHETIC=True,
    NGAO_REPLY_READER="rules",
    NGAO_ALERTS_REQUIRE_APPROVAL=True,
    NGAO_SEND_WELCOME_SMS=True,
    NGAO_SEND_REPORT_ACK=True,
    NGAO_DEMO_PHONES=["+254711000001"],
    NGAO_TEMPLATE_HAZARDS=None,
)


@override_settings(**TEST_SETTINGS)
class NgaoTestCase(TestCase):
    """Synthetic demo dataset + reference data, and API clients for each caller."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", templates=True, verbosity=0, stdout=open("/dev/null", "w"))
        demo_data.build()

    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {API_TOKEN}")
        self.ussd = APIClient()
        self.ussd.credentials(HTTP_AUTHORIZATION=f"Bearer {USSD_TOKEN}")
        self.anon = APIClient()

    def job(self, name, body=None, expect=202):
        response = self.api.post(f"/ngao/v1/jobs/{name}", body or {}, format="json")
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def score_today(self):
        return self.job("score")["summary"]
