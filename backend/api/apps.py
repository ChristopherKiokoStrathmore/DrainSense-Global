from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "api"

    def ready(self):
        from api import openapi  # noqa: F401  registers the bearer-token OpenAPI scheme
