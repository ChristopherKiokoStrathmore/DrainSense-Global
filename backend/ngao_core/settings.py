"""Django settings for the Ngao backend (APIs, alert engine, Africa's Talking SMS, reply reader, dryer router).

Everything that differs between a laptop and the OCI VM comes from environment variables
(see .env.example). Defaults are safe for local development: SQLite, console SMS, rule-based
reply reader.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

from ngao_core.env import env_bool, env_float, env_int, env_json, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", False)
TESTING = sys.argv[1:2] == ["test"]
SECRET_KEY = env_str("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if DEBUG or TESTING else None)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", False)

INSTALLED_APPS = [
    "django.contrib.staticfiles",  # Swagger UI / ReDoc assets (served by WhiteNoise, no CDN needed)
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "ngao",
    "scoring",
    "alerts",
    "sms",
    "reply_reader",
    "ussd_api",
    "routing",
    "seasonal",
    "api",
]

MIDDLEWARE = [
    "ngao_core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "ngao_core.urls"
APPEND_SLASH = False
WSGI_APPLICATION = "ngao_core.wsgi.application"
ASGI_APPLICATION = "ngao_core.asgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {}}]

STATIC_URL = "static/"
STATIC_ROOT = env_str("STATIC_ROOT")  # only for collectstatic (the Docker image sets it)
WHITENOISE_USE_FINDERS = True  # serve the docs assets straight from installed apps

# ---------------------------------------------------------------------------
# Database. SQLite for development and tests; Oracle Autonomous Database (schema NGAO) in
# production. In Oracle the tables come from db/schema.sql, so `migrate` is never run there.
# ---------------------------------------------------------------------------
DB_ENGINE = env_str("DB_ENGINE", "sqlite").lower()
DB_SCHEMA = env_str("DB_SCHEMA")  # ALTER SESSION SET CURRENT_SCHEMA when the login user differs

if DB_ENGINE == "oracle":
    oracle_options = {}
    wallet_dir = env_str("DB_WALLET_DIR")
    if wallet_dir:
        oracle_options.update(
            config_dir=wallet_dir,
            wallet_location=wallet_dir,
            wallet_password=env_str("DB_WALLET_PASSWORD"),
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.oracle",
            "NAME": env_str("DB_DSN"),  # TNS alias (e.g. ngaodb_low) or full connect descriptor
            "USER": env_str("DB_USER", "NGAO"),
            "PASSWORD": env_str("DB_PASSWORD"),
            "OPTIONS": oracle_options,
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env_str("SQLITE_PATH", str(BASE_DIR / "ngao_dev.sqlite3")),
            # The background SMS sender writes while requests do: WAL + IMMEDIATE transactions and a
            # longer busy timeout stop "database is locked" on a laptop demo.
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE",
                        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Nairobi"  # all JSON times are rendered with +03:00
USE_I18N = False
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["ngao_core.auth.BearerTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["ngao_core.auth.IsApiClient"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "ngao_core.errors.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
    "UNAUTHENTICATED_TOKEN": None,
    "DATETIME_FORMAT": "iso-8601",
    "COERCE_DECIMAL_TO_STRING": False,
}

from ngao_core.openapi import SPECTACULAR_SETTINGS  # noqa: E402,F401  (OpenAPI / Swagger UI)

NGAO_API_DOCS_PUBLIC = env_bool("API_DOCS_PUBLIC", True)  # /ngao/v1/docs without a token

LOG_LEVEL = env_str("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "ngao_core.middleware.RequestIdLogFilter"}},
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain", "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# ---------------------------------------------------------------------------
# Ngao settings (API_CONTRACT.md). Secrets only ever come from the environment.
# ---------------------------------------------------------------------------

# Auth (§2.0): Louisa's USSD service and APEX / demo controls / router each get bearer tokens.
NGAO_USSD_SERVICE_TOKEN = env_str("USSD_SERVICE_TOKEN")
NGAO_API_TOKENS = env_list("NGAO_API_TOKENS")
NGAO_WEBHOOK_SECRET = env_str("WEBHOOK_SECRET")

# SMS provider (§3). console logs only; africastalking is the live provider; wasiliana is a fallback.
NGAO_SMS_BACKEND = env_str("SMS_BACKEND", "console")  # console | africastalking | wasiliana

# Africa's Talking. AT_USERNAME "sandbox" uses the sandbox API (messages show in the AT simulator).
NGAO_AT_USERNAME = env_str("AT_USERNAME", "sandbox")
NGAO_AT_API_KEY = env_str("AT_API_KEY")
NGAO_AT_SENDER_ID = env_str("AT_SENDER_ID")  # registered alphanumeric sender ID or shortcode; empty = AT default
NGAO_AT_SMS_URL = env_str("AT_SMS_URL")  # override; default chosen from AT_USERNAME
NGAO_AT_TIMEOUT = env_float("AT_TIMEOUT_SECONDS", 10.0)

# Wasiliana (fallback). Reference shape from pytek-wasiliana; not confirmed at docs.wasiliana.com.
NGAO_WASILIANA_API_KEY = env_str("WASILIANA_API_KEY")
NGAO_WASILIANA_SENDER_ID = env_str("WASILIANA_SENDER_ID")
NGAO_WASILIANA_SMS_URL = env_str("WASILIANA_SMS_URL", "https://api.wasiliana.com/api/v1/developer/send/sms")
NGAO_WASILIANA_TIMEOUT = env_float("WASILIANA_TIMEOUT_SECONDS", 10.0)

# Extra callback field names for the active provider (JSON: our name -> list of their keys).
NGAO_SMS_DLR_FIELDS = env_json("SMS_DLR_FIELDS", None)
NGAO_SMS_INBOUND_FIELDS = env_json("SMS_INBOUND_FIELDS", None)

# inline: a background thread sends right after a message is queued (good for the demo).
# worker: only `manage.py sms_worker` sends.
NGAO_SMS_DISPATCH = env_str("SMS_DISPATCH", "inline")
NGAO_SMS_BATCH_SIZE = env_int("SMS_BATCH_SIZE", 50)
NGAO_SMS_RETRY_HOURS = env_int("SMS_RETRY_HOURS", 6)  # give up on transient send errors after this
# Synthetic subscribers (is_demo = 'Y', fake +2547000... numbers) never reach the provider: their
# messages are marked SENT with provider_msg_id SIMULATED. Phones in DEMO_PHONES are always real.
NGAO_SMS_SIMULATE_SYNTHETIC = env_bool("SMS_SIMULATE_SYNTHETIC", True)
NGAO_ASYNC_TASKS = env_bool("NGAO_ASYNC_TASKS", True)

# Alert engine (ARCHITECTURE §5). Officers approve in APEX (Alert Approvals page) unless off.
NGAO_ALERTS_REQUIRE_APPROVAL = env_bool("ALERTS_REQUIRE_APPROVAL", True)
NGAO_ALERT_MAX_AGE_HOURS = env_int("ALERT_MAX_AGE_HOURS", 48)
NGAO_WEEKLY_ROUTINE_CAP = env_int("WEEKLY_ROUTINE_CAP", 3)
NGAO_TEMPLATE_HAZARDS = env_json("NGAO_TEMPLATE_HAZARDS", None)  # {"A1": "dry_window", ...}
NGAO_TEMPLATE_SEVERITY_ACT = env_str("NGAO_TEMPLATE_SEVERITY_ACT", "warning")
NGAO_SEND_WELCOME_SMS = env_bool("SEND_WELCOME_SMS", True)
NGAO_SEND_REPORT_ACK = env_bool("SEND_REPORT_ACK", True)

# Scoring fallback (Trevor's models replace this; see scoring/heuristics.py).
NGAO_SCORER_MODEL_VERSION = env_str("SCORER_MODEL_VERSION", "heuristic-v0-backend")

# Reply reader (§2.4): genai (OCI Generative AI) or rules (keyword + number fallback).
NGAO_REPLY_READER = env_str("REPLY_READER", "rules")
NGAO_AI_CONFIDENCE_THRESHOLD = env_float("AI_CONFIDENCE_THRESHOLD", 0.7)
NGAO_OCI_AUTH = env_str("OCI_AUTH", "config")  # config | instance_principal | resource_principal
NGAO_OCI_CONFIG_FILE = env_str("OCI_CONFIG_FILE", "~/.oci/config")
NGAO_OCI_PROFILE = env_str("OCI_PROFILE", "DEFAULT")
NGAO_OCI_REGION = env_str("OCI_REGION", "eu-frankfurt-1")
NGAO_OCI_GENAI_ENDPOINT = env_str("OCI_GENAI_ENDPOINT")
NGAO_OCI_COMPARTMENT_ID = env_str("OCI_COMPARTMENT_ID")
NGAO_OCI_GENAI_MODEL_ID = env_str("OCI_GENAI_MODEL_ID", "cohere.command-a-03-2025")
NGAO_OCI_GENAI_TIMEOUT = env_float("OCI_GENAI_TIMEOUT_SECONDS", 8.0)

# Dryer router (ARCHITECTURE §6.3).
NGAO_ROUTER_MIN_STOP_BAGS = env_int("ROUTER_MIN_STOP_BAGS", 10)
NGAO_ROUTER_START_HOUR = env_int("ROUTER_START_HOUR", 8)
NGAO_ROUTER_SPEED_KMH = env_float("ROUTER_SPEED_KMH", 30.0)
NGAO_ROUTER_BAG_TONNES = env_float("ROUTER_BAG_TONNES", 0.09)
NGAO_ROUTER_DEFAULT_RAIN_PROB = env_float("ROUTER_DEFAULT_RAIN_PROB", 0.5)
NGAO_ROUTER_DEFAULT_MOISTURE_PCT = env_float("ROUTER_DEFAULT_MOISTURE_PCT", 20.0)
NGAO_ROUTER_TARGET_MOISTURE_PCT = env_float("ROUTER_TARGET_MOISTURE_PCT", 13.0)
NGAO_ROUTER_KES_PER_POINT = env_float("ROUTER_KES_PER_MOISTURE_POINT", 30.0)

# Seasonal outlook (seasonal/, the v3 El Niño / Indian Ocean model; docs/SEASONAL_MODEL.md).
NGAO_SEASONAL_ARTIFACT = env_str("SEASONAL_ARTIFACT", str(BASE_DIR / "seasonal" / "model" / "ngao_seasonal_v3.json"))
NGAO_SEASONAL_CACHE_DIR = env_str("SEASONAL_CACHE_DIR", str(BASE_DIR / ".cache" / "seasonal"))
# auto: the full 6-index model when ERA5 is configured, else the SST-only variant. sst | era5 force one.
NGAO_SEASONAL_FEATURE_SOURCE = env_str("SEASONAL_FEATURE_SOURCE", "auto")
NGAO_CDSAPI_URL = env_str("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
NGAO_CDSAPI_KEY = env_str("CDSAPI_KEY")

# Demo (§2.2 reset-demo). Presenter phones are wiped by reset-demo and flagged is_demo on register.
NGAO_DEMO_PHONES = env_list("DEMO_PHONES")
