from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created


def _init_oracle_session(sender, connection, **kwargs):
    """Runs after Django's own session setup on every new Oracle connection.

    - TIME_ZONE: Django sets the region 'UTC'; TIMESTAMP WITH TIME ZONE values written in a
      region-named session cannot be read back by python-oracledb thin mode (DPY-3022), so the
      session uses the offset '+00:00' instead.
    - CURRENT_SCHEMA: log in as one user, work in schema NGAO (or WKSP_NGAO, the APEX schema).
    """
    if connection.vendor != "oracle":
        return
    statements = ["ALTER SESSION SET TIME_ZONE = '+00:00'"]
    schema = settings.DB_SCHEMA
    if schema:
        if not schema.replace("_", "").isalnum():
            raise ValueError("DB_SCHEMA must be a plain Oracle identifier")
        statements.append(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


class NgaoConfig(AppConfig):
    name = "ngao"
    verbose_name = "Ngao shared schema"

    def ready(self):
        connection_created.connect(_init_oracle_session, dispatch_uid="ngao-oracle-session")
