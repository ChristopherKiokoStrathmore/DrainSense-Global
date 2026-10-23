"""The scheduler runs each daily job once, even without JOB_RUNS (the live NgaoDB has none)."""
import datetime as dt
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase

from ngao.management.commands.scheduler import Command
from ngao_core.timeutil import EAT


def at(hour, minute, day=dt.date(2026, 10, 15)):
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=EAT)


@mock.patch("ngao.management.commands.scheduler.job_runs_available", return_value=False)
class SchedulerTests(SimpleTestCase):
    def setUp(self):
        self.command = Command(stdout=StringIO())
        self.calls = []
        self.command._run = lambda job: self.calls.append(job) or {"ok": True}

    def test_job_runs_once_per_day_without_job_runs(self, _available):
        for minute in range(0, 120, 1):  # every tick through the two-hour catch-up window
            self.command.tick(at(18, 0) + dt.timedelta(minutes=minute), {})
        self.assertEqual(self.calls.count("plan-routes"), 1)

    def test_runs_again_the_next_day(self, _available):
        self.command.tick(at(6, 1), {})
        self.command.tick(at(6, 1, day=dt.date(2026, 10, 16)), {})
        self.assertEqual(self.calls.count("send-alerts"), 2)

    def test_failure_retries_after_ten_minutes_not_every_tick(self, _available):
        attempts = []

        def failing(job):
            attempts.append(job)
            raise RuntimeError("NgaoDB stopped")

        self.command._run = failing
        for second in range(0, 600, 15):
            self.command.tick(at(18, 0) + dt.timedelta(seconds=second), {})
        self.assertEqual(attempts.count("plan-routes"), 1)
        self.command.tick(at(18, 10), {})
        self.assertEqual(attempts.count("plan-routes"), 2)

    def test_outside_window_nothing_runs(self, _available):
        self.command.tick(at(12, 0), {})
        self.assertEqual(self.calls, [])

    def test_scoring_needs_the_flag(self, _available):
        self.command.tick(at(5, 31), {})
        self.assertNotIn("score", self.calls)
        self.command.tick(at(5, 32), {"with_scoring": True})
        self.assertIn("score", self.calls)

    def test_seasonal_slot(self, _available):
        self.command.tick(at(7, 29), {})
        self.assertNotIn("seasonal-outlook", self.calls)
        self.command.tick(at(7, 31), {})
        self.command.tick(at(7, 45), {})
        self.assertEqual(self.calls.count("seasonal-outlook"), 1)
