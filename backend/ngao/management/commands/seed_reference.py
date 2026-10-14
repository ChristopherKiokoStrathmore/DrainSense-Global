"""Insert the alert catalogue (ALERT_TYPES A1..C3) and, optionally, the default SMS copy into
ALERT_TEMPLATE. Existing rows are never changed, so it is safe to run against the live ADB."""
from django.core.management.base import BaseCommand
from django.db import transaction

from alerts.catalogue import ALERT_TYPES, DEFAULT_ALERT_COPY
from alerts.templates import hazard_for
from ngao.models import AlertTemplate, AlertType


class Command(BaseCommand):
    help = "Seed ALERT_TYPES (and with --templates, ALERT_TEMPLATE) without overwriting existing rows."

    def add_arguments(self, parser):
        parser.add_argument("--templates", action="store_true", help="also insert missing ALERT_TEMPLATE rows")
        parser.add_argument("--severity", default=None, help="ALERT_TEMPLATE.severity for the rows (default: NGAO_TEMPLATE_SEVERITY_ACT)")

    @transaction.atomic
    def handle(self, *args, **options):
        from django.conf import settings

        created = 0
        for code, service, name, act, watch, cooldown, overrides, audience in ALERT_TYPES:
            copy = DEFAULT_ALERT_COPY.get(code, {})
            _, was_created = AlertType.objects.get_or_create(
                alert_type=code,
                defaults=dict(service=service, name=name, act_threshold=act, watch_threshold=watch,
                              cooldown_hours=cooldown, overrides_cap=overrides, audience_roles=audience,
                              template_sw=copy.get("sw"), template_en=copy.get("en")),
            )
            created += was_created
        self.stdout.write(f"ALERT_TYPES: {created} inserted, {len(ALERT_TYPES) - created} already present")

        if options["templates"]:
            severity = options["severity"] or settings.NGAO_TEMPLATE_SEVERITY_ACT
            inserted = 0
            for code, copy in DEFAULT_ALERT_COPY.items():
                for lang, body in copy.items():
                    if len(body) > 160:
                        self.stderr.write(f"skip {code}/{lang}: {len(body)} chars > 160")
                        continue
                    _, was_created = AlertTemplate.objects.get_or_create(
                        hazard=hazard_for(code), severity=severity, lang=lang, defaults={"body": body})
                    inserted += was_created
            self.stdout.write(f"ALERT_TEMPLATE: {inserted} inserted")
