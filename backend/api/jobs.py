"""Action jobs (API_CONTRACT §2.2): run synchronously, recorded in JOB_RUNS.

Called by the APEX Demo controls page, the scheduler (manage.py scheduler / cron) and
`manage.py run_job`. Each job returns a summary dict that goes into the 202 response.
"""
import datetime as dt
import json
import logging

from django.core.serializers.json import DjangoJSONEncoder
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


def run_job(name, params):
    """Run one job and record it. Raises the job's exception after marking the row FAILED."""
    job = JobRun.objects.create(job_name=name, params=_jsonable(params), status=JobRun.RUNNING)
    try:
        summary = JOBS[name](params)
    except Exception as exc:
        job.status = JobRun.FAILED
        job.error_text = (exc.message if isinstance(exc, ApiError) else repr(exc))[:1000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_text", "finished_at"])
        if not isinstance(exc, ApiError):
            logger.exception("job %s (%s) failed", name, job.job_id)
        raise
    job.status = JobRun.DONE
    job.summary = _jsonable(summary)
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "summary", "finished_at"])
    return job
