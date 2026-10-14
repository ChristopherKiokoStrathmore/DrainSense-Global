"""Run slow work (Wasiliana sends, GenAI parses) after the HTTP response.

With NGAO_ASYNC_TASKS on, work runs on a daemon thread after the current transaction commits,
so webhooks return 200 at once and USSD endpoints stay well under two seconds. Tests turn it off
and the work runs inline.
"""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connections, transaction

logger = logging.getLogger(__name__)


def _run(fn, args, kwargs):
    close_old_connections()
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.exception("background task %s failed", getattr(fn, "__name__", fn))
    finally:
        connections.close_all()


def run_after_commit(fn, *args, **kwargs):
    if not settings.NGAO_ASYNC_TASKS:
        fn(*args, **kwargs)
        return

    def start():
        threading.Thread(target=_run, args=(fn, args, kwargs), daemon=True).start()

    transaction.on_commit(start)
