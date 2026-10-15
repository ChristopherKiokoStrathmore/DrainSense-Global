"""Action jobs (API_CONTRACT §2.2): run synchronously.

Called by the APEX Demo controls page, the scheduler (manage.py scheduler / cron) and
`manage.py run_job`. Each job returns a summary dict that goes into the 202 response.

JOB_RUNS is not a live NgaoDB table. When it exists (local SQLite, or a database that
created it) the run is stored there. When it does not, the job still runs and the result
is kept in this process so GET /jobs/{id}/status works until restart.
"""
import datetime as dt
import itertools
import json
import logging
import threading
from types import SimpleNamespace

from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, connection
from django.utils import timezone

from ngao.models import JobRun
from ngao_core.errors import ApiError
from ngao_core.timeutil import today_eat

logger = logging.getLogger(__name__)


def _jsonable(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def job_score(p):
    from scoring.service import run_scoring

    return run_scoring(p.get("as_of") or today_eat(), county=p.get("county"), alert_types=p.get("alert_types"),
                       overwrite=p.get("overwrite", False), dry_run=p.get("dry_run", False))


def job_send_alerts(p):
    from alerts.engine import dispatch_approved, draft_alerts

    summary = draft_alerts(p.get("as_of") or today_eat(), dry_run=p.get("dry_run", False), county=p.get("county"))
    if not p.get("dry_run", False):
        summary["approved_dispatch"] = dispatch_approved()
    return summary


def job_dispatch(p):
    """Fan out alerts officers approved in APEX, then send queued SMS now."""
    from alerts.engine import dispatch_approved
    from sms.dispatcher import send_queued

    if p.get("retry_failed"):
        from ngao.models import Message

        since = timezone.now() - dt.timedelta(hours=24)
        Message.objects.filter(direction="OUT", status="FAILED", created_at__gte=since).update(status="QUEUED")
    summary = {"approved_dispatch": dispatch_approved()}
    summary["sms"] = send_queued(limit=p.get("limit"))
    return summary


def job_plan_routes(p):
    from routing.service import plan_routes

    return plan_routes(p.get("route_date") or today_eat() + dt.timedelta(days=1),
                       replace=p.get("replace", True), notify=p.get("notify", True))


def job_replay(p):
    from scoring.service import run_replay

    return run_replay(p.get("scenario") or "garissa_rvf_2006", p.get("from"), p.get("to"),
                      overwrite=p.get("overwrite", False))


def job_reset_demo(p):
    from ngao.demo import reset_demo

    return reset_demo(p.get("phones"), alerts_today=p.get("alerts_today", True), routes=p.get("routes", True))


def job_grade_outcomes(p):
    from alerts.scorecard import grade_outcomes

    return grade_outcomes(p.get("as_of") or today_eat())


JOBS = {
    "score": job_score,
    "send-alerts": job_send_alerts,
    "plan-routes": job_plan_routes,
    "replay": job_replay,
    "reset-demo": job_reset_demo,
    "dispatch": job_dispatch,
    "grade-outcomes": job_grade_outcomes,
}


_memory_jobs = {}
_memory_ids = itertools.count(1)
_memory_lock = threading.Lock()


def _missing_relation(exc):
    text = str(exc).lower()
    return "ora-00942" in text or "no such table" in text or "job_runs" in text


def job_runs_available():
    """True when this connection can see JOB_RUNS. Cached on the connection."""
    cached = getattr(connection, "_ngao_job_runs", None)
    if cached is not None:
        return cached
    available = False
    try:
        with connection.cursor() as cursor:
            if connection.vendor == "oracle":
                cursor.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'JOB_RUNS'")
                available = bool(cursor.fetchone()[0])
            else:
                available = "job_runs" in connection.introspection.table_names(cursor)
    except DatabaseError:
        available = False
    connection._ngao_job_runs = available
    return available


def _remember(job):
    with _memory_lock:
        _memory_jobs[job.job_id] = job
    return job


def _new_memory_job(name, params):
    now = timezone.now()
    with _memory_lock:
        job_id = next(_memory_ids)
    return SimpleNamespace(
        job_id=job_id, job_name=name, params=_jsonable(params), status=JobRun.RUNNING,
        summary=None, error_text=None, started_at=now, finished_at=None,
    )


def _finish(job, *, persist):
    if not persist:
        return _remember(job)
    job.save(update_fields=["status", "summary", "error_text", "finished_at"])
    return job


def _execute(name, params, job, persist):
    try:
        summary = JOBS[name](params)
    except Exception as exc:
        job.status = JobRun.FAILED
        job.error_text = (exc.message if isinstance(exc, ApiError) else repr(exc))[:1000]
        job.finished_at = timezone.now()
        _finish(job, persist=persist)
        if not isinstance(exc, ApiError):
            logger.exception("job %s (%s) failed", name, job.job_id)
        raise
    job.status = JobRun.DONE
    job.summary = _jsonable(summary)
    job.finished_at = timezone.now()
    return _finish(job, persist=persist)


def get_job(job_id):
    """The stored run, from JOB_RUNS or from this process when that table is absent."""
    if job_runs_available():
        return JobRun.objects.filter(pk=job_id).first()
    with _memory_lock:
        return _memory_jobs.get(int(job_id))


def run_job(name, params):
    """Run one job and record it. Raises the job's exception after marking the run FAILED."""
    if not job_runs_available():
        logger.info("JOB_RUNS is not in this database; job %s will not be persisted", name)
        return _execute(name, params, _new_memory_job(name, params), persist=False)
    try:
        job = JobRun.objects.create(job_name=name, params=_jsonable(params), status=JobRun.RUNNING)
    except DatabaseError as exc:
        if not _missing_relation(exc):
            raise
        connection._ngao_job_runs = False
        logger.warning("JOB_RUNS is not in this database (%s); running %s without persisting", exc, name)
        return _execute(name, params, _new_memory_job(name, params), persist=False)
    return _execute(name, params, job, persist=True)
