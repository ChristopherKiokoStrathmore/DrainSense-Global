"""Compare the backend's models with the connected database (run it against the live ADB).

Prints every table and column the backend reads or writes that is missing, and the allowed
values of the ALERTS.status / MESSAGES.status CHECK constraints, so schema drift between
db/schema.sql and what is actually deployed shows up in seconds instead of during the demo.
Exit code 1 when something is missing.
"""
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

NGAO_APPS = ("ngao",)


def _db_columns(table):
    with connection.cursor() as cursor:
        if connection.vendor == "oracle":
            owner = (settings.DB_SCHEMA or settings.DATABASES["default"]["USER"] or "").upper()
            cursor.execute(
                "SELECT column_name FROM all_tab_columns WHERE owner = %s AND table_name = %s",
                [owner, table.upper()],
            )
            return {row[0].lower() for row in cursor.fetchall()}
        if table not in connection.introspection.table_names(cursor):
            return set()
        return {col.name.lower() for col in connection.introspection.get_table_description(cursor, table)}


def _check_conditions(table):
    if connection.vendor != "oracle":
        return []
    owner = (settings.DB_SCHEMA or settings.DATABASES["default"]["USER"] or "").upper()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT search_condition_vc FROM all_constraints "
            "WHERE owner = %s AND table_name = %s AND constraint_type = 'C'",
            [owner, table.upper()],
        )
        return [row[0] for row in cursor.fetchall() if row[0] and "NOT NULL" not in row[0].upper()]


def _region_named_timestamps():
    """TIMESTAMP WITH TIME ZONE values stored with a region name (fix: migration 001 step 4)."""
    if connection.vendor != "oracle":
        return []
    found = []
    with connection.cursor() as cursor:
        for model in apps.get_app_config("ngao").get_models():
            for field in model._meta.concrete_fields:
                if field.get_internal_type() != "DateTimeField":
                    continue
                table, column = model._meta.db_table.upper(), field.column.upper()
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE EXTRACT(TIMEZONE_REGION FROM {column}) <> 'UNKNOWN'"
                    )
                    count = cursor.fetchone()[0]
                except DatabaseError:  # not a WITH TIME ZONE column, or missing (reported above)
                    continue
                if count:
                    found.append((table, column, count))
    return found


class Command(BaseCommand):
    help = "Check that every table/column the backend uses exists in the connected database."

    def handle(self, *args, **options):
        problems = 0
        for model in apps.get_app_config("ngao").get_models():
            table = model._meta.db_table
            existing = _db_columns(table)
            if not existing:
                self.stdout.write(self.style.ERROR(f"MISSING TABLE {table.upper()}"))
                problems += 1
                continue
            needed = {f.column.lower() for f in model._meta.concrete_fields}
            missing = sorted(needed - existing)
            if missing:
                problems += len(missing)
                self.stdout.write(self.style.ERROR(f"{table.upper()}: missing columns {', '.join(missing)}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"{table.upper()}: ok ({len(needed)} columns)"))
        for table in ("alerts", "messages", "reports", "subscribers"):
            for condition in _check_conditions(table):
                self.stdout.write(f"  CHECK on {table.upper()}: {condition}")
        for table, column, count in _region_named_timestamps():
            problems += 1
            self.stdout.write(self.style.ERROR(
                f"{table}.{column}: {count} rows use a named time zone (unreadable in thin mode, DPY-3022)"))
        if problems:
            raise CommandError(f"{problems} schema problem(s); see db/migrations/001_backend_alignment.sql")
        self.stdout.write(self.style.SUCCESS("schema matches the backend models"))
