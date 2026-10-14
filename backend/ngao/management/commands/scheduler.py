"""The daily cycle from the README (EAT), for a VM without DBMS_SCHEDULER access to the backend:

  05:30  score            only with --with-scoring (Trevor's pipeline normally writes RISK_SCORES)
  06:00  send-alerts      draft ACT alerts (officers approve in APEX; worker sends)
  18:00  plan-routes      tomorrow's dryer routes + booking SMS
  Mon 07:00 grade-outcomes
  every --interval s      approved-alert fan-out and the SMS queue (same as sms_worker)

A job runs at most once per day: on start-up the JOB_RUNS table tells the scheduler what already
ran today, so a restart does not re-send.
"""
import datetime as dt
import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from alerts.engine import dispatch_approved
from api.jobs import run_job
from api.serializers import parse_job_params
from ngao.models import JobRun
from ngao_core.timeutil import now_eat, start_of_day_eat
from sms.dispatcher import send_queued

logger = logging.getLogger(__name__)

SCHEDULE = [
    # (job, hour, minute, weekday or None, needs flag)
    ("score", 5, 30, None, "with_scoring"),
    ("send-alerts", 6, 0, None, None),
    ("plan-routes", 18, 0, None, None),
    ("grade-outcomes", 7, 0, 0, None),
]
CATCH_UP = dt.timedelta(hours=2)


class Command(BaseCommand):
    help = "Run the Ngao daily cycle (send alerts 06:00, plan routes 18:00, ...) and the SMS queue."

    def add_arguments(self, parser):
        parser.add_argument("--with-scoring", action="store_true", help="also run the fallback scorer at 05:30")
        parser.add_argument("--interval", type=float, default=15.0)

    def _ran_today(self, job, today):
        return JobRun.objects.filter(job_name=job, started_at__gte=start_of_day_eat(today)).exists()

    def handle(self, *args, **options):
        stopping = {"flag": False}
        signal.signal(signal.SIGTERM, lambda *a: stopping.update(flag=True))
        signal.signal(signal.SIGINT, lambda *a: stopping.update(flag=True))
        self.stdout.write("scheduler started (EAT)")
        while not stopping["flag"]:
            close_old_connections()
            now = now_eat()
            for job, hour, minute, weekday, flag in SCHEDULE:
                if flag and not options[flag]:
                    continue
                if weekday is not None and now.weekday() != weekday:
                    continue
                due = dt.datetime.combine(now.date(), dt.time(hour, minute), tzinfo=now.tzinfo)
                # Catch up for two hours after the slot (a restart), never later in the day.
                if due <= now < due + CATCH_UP and not self._ran_today(job, now.date()):
                    try:
                        run = run_job(job, parse_job_params(job, {}))
                        self.stdout.write(f"{job}: {run.summary}")
                    except Exception:
                        logger.exception("scheduled job %s failed", job)
            try:
                dispatch_approved()
                send_queued()
            except Exception:
                logger.exception("dispatch cycle failed")
            time.sleep(options["interval"])
