"""Load the SYNTHETIC local demo dataset (ngao/demo_data.py). Refuses to touch Oracle by default:
the ADB already holds the real wards, features and the team's synthetic subscribers."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ngao import demo_data


class Command(BaseCommand):
    help = "Seed reference data + a synthetic demo dataset for local development."

    def add_arguments(self, parser):
        parser.add_argument("--allow-oracle", action="store_true",
                            help="really write synthetic rows into the connected Oracle database")

    def handle(self, *args, **options):
        if connection.vendor == "oracle" and not options["allow_oracle"]:
            raise CommandError("refusing to load synthetic data into Oracle without --allow-oracle")
        call_command("seed_reference", templates=True, stdout=self.stdout)
        counts = demo_data.build()
        self.stdout.write(self.style.SUCCESS(f"synthetic demo data loaded: {counts}"))
