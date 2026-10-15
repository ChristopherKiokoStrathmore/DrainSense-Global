"""Record where APEX reaches this backend: NGAO_BACKEND_CONFIG.BASE_URL (db/deploy/020_backend_bridge.sql).

The VM deploy runs it after the containers start:
    python manage.py set_backend_config --base-url https://<host>/ngao/v1

The table only exists in NgaoDB. Elsewhere (SQLite) the command reports that and exits 0.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

TABLE = "ngao_backend_config"


class Command(BaseCommand):
    help = "Write BASE_URL into NGAO_BACKEND_CONFIG so APEX's NGAO_BACKEND package can call this API."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True, help="https://<host>/ngao/v1")

    def handle(self, *args, base_url, **options):
        base_url = base_url.rstrip("/")
        if not base_url.startswith("https://"):
            raise CommandError("APEX on Autonomous Database only calls HTTPS URLs; pass https://<host>/ngao/v1")
        if not self._table_exists():
            self.stdout.write(f"{TABLE} not found in this database; nothing to do "
                              "(it is created by db/deploy/020_backend_bridge.sql on NgaoDB)")
            return
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"UPDATE {TABLE} SET config_value = %s WHERE config_key = 'BASE_URL'", [base_url])
            if cursor.rowcount == 0:
                cursor.execute(f"INSERT INTO {TABLE} (config_key, config_value) VALUES ('BASE_URL', %s)", [base_url])
        self.stdout.write(f"NGAO_BACKEND_CONFIG.BASE_URL = {base_url}")

    def _table_exists(self):
        with connection.cursor() as cursor:
            if connection.vendor == "oracle":
                # The login user may be ADMIN working in DB_SCHEMA (CURRENT_SCHEMA), so look there.
                owner = (settings.DB_SCHEMA or settings.DATABASES["default"]["USER"] or "").upper()
                cursor.execute("SELECT COUNT(*) FROM all_tables WHERE owner = %s AND table_name = %s",
                               [owner, TABLE.upper()])
                return cursor.fetchone()[0] > 0
            return TABLE in {name.lower() for name in connection.introspection.table_names(cursor)}
