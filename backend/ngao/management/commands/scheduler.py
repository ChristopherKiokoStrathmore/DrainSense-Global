"""The daily cycle from the README (EAT), for a VM without DBMS_SCHEDULER access to the backend:

  05:30  score            only with --with-scoring (Trevor's pipeline normally writes RISK_SCORES)
  06:00  send-alerts      draft ACT alerts (officers approve in APEX; worker sends)
  18:00  plan-routes      tomorrow's dryer routes + booking SMS
  07:30  seasonal-outlook from October, once per season (when the September SST is out)
  Mon 07:00 grade-outcomes
  every --interval s      approved-alert fan-out and the SMS queue (same as sms_worker)

A job runs at most once per day. This process remembers what it ran; when JOB_RUNS exists, that
table also tells a restarted scheduler what already ran today. The live database has no JOB_RUNS
table, so a restart inside a slot's two-hour catch-up window may run that day's job once more.
A failed job is retried after RETRY_AFTER, not on every tick.
"""
import datetime as dt
import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from alerts.engine import dispatch_approved
from api.jobs import job_runs_available, run_job
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
    ("seasonal-outlook", 7, 30, None, None),
]
CATCH_UP = dt.timedelta(hours=2)
RETRY_AFTER = dt.timedelta(minutes=10)


class Command(BaseCommand):
    help = "Run the Ngao daily cycle (send alerts 06:00, plan routes 18:00, ...) and the SMS queue."

    def add_arguments(self, parser):
        parser.add_argument("--with-scoring", action="store_true", help="also run the fallback scorer at 05:30")
        parser.add_argument("--interval", type=float, default=15.0)

    def _ran_today(self, job, today):
        if self.done.get(job) == today:
            return True
        if not job_runs_available():
            return False
        return JobRun.objects.filter(job_name=job, started_at__gte=start_of_day_eat(today)).exists()

    def _run(self, job):
        if job == "seasonal-outlook":
            from seasonal.service import issue_if_due

            return issue_if_due()  # None outside October-December or once the season is issued
        return run_job(job, parse_job_params(job, {})).summary

    def tick(self, now, options):
        """Run every job whose slot is due and has not run today. Returns the jobs that ran."""
        ran = []
        for job, hour, minute, weekday, flag in SCHEDULE:
            if flag and not options.get(flag):
                continue
            if weekday is not None and now.weekday() != weekday:
                continue
            due = dt.datetime.combine(now.date(), dt.time(hour, minute), tzinfo=now.tzinfo)
            # Catch up for two hours after the slot (a restart), never later in the day.
            if not (due <= now < due + CATCH_UP) or self._ran_today(job, now.date()):
                continue
            if self.retry_at.get(job) and now < self.retry_at[job]:
                continue
            try:
                summary = self._run(job)
            except Exception:
                logger.exception("scheduled job %s failed; retrying after %s", job, RETRY_AFTER)
                self.retry_at[job] = now + RETRY_AFTER
                continue
            self.done[job] = now.date()
            self.retry_at.pop(job, None)
            ran.append(job)
            self.stdout.write(f"{job}: {summary}")
        return ran

    def handle(self, *args, **options):
        stopping = {"flag": False}
        signal.signal(signal.SIGTERM, lambda *a: stopping.update(flag=True))
        signal.signal(signal.SIGINT, lambda *a: stopping.update(flag=True))
        self.stdout.write("scheduler started (EAT)")
        while not stopping["flag"]:
            close_old_connections()
            self.tick(now_eat(), options)
            try:
                dispatch_approved()
                send_queued()
            except Exception:
                logger.exception("dispatch cycle failed")
            time.sleep(options["interval"])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.done = {}      # job -> EAT date it last succeeded in this process
        self.retry_at = {}  # job -> earliest retry after a failure
