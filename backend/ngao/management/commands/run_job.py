"""Run a §2.2 job from the shell or cron: manage.py run_job send-alerts --param as_of=2026-10-15"""
import json

from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError

from api.jobs import JOBS, run_job
from api.serializers import parse_job_params
from ngao_core.errors import ApiError


class Command(BaseCommand):
    help = f"Run one action job: {', '.join(sorted(JOBS))}"

    def add_arguments(self, parser):
        parser.add_argument("job", choices=sorted(JOBS))
        parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                            help="job parameter (repeatable); VALUE may be JSON, e.g. dry_run=true")
        parser.add_argument("--json", default=None, help="all parameters as one JSON object")

    def handle(self, *args, **options):
        params = json.loads(options["json"]) if options["json"] else {}
        for item in options["param"]:
            key, _, value = item.partition("=")
            try:
                params[key] = json.loads(value)
            except json.JSONDecodeError:
                params[key] = value
        try:
            run = run_job(options["job"], parse_job_params(options["job"], params))
        except ValidationError as exc:
            raise CommandError(f"invalid parameters: {exc.detail}") from exc
        except ApiError as exc:
            raise CommandError(f"{exc.error_code}: {exc.message}") from exc
        self.stdout.write(json.dumps({"job_id": run.job_id, "job": options["job"], "status": run.status,
                                      "summary": run.summary}, indent=2))
