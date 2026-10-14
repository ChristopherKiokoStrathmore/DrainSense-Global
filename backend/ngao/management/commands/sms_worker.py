"""SMS worker: fan out APEX-approved alerts and drain the MESSAGES queue every few seconds.

Run it as its own process (systemd / docker compose) when SMS_DISPATCH=worker, or alongside the
inline sender: rows are claimed with SKIP LOCKED, so nothing is sent twice.
"""
import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from alerts.engine import dispatch_approved
from sms.dispatcher import send_queued

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Poll for approved alerts and queued SMS, and send them through Wasiliana."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
        parser.add_argument("--once", action="store_true", help="run one cycle and exit")

    def handle(self, *args, **options):
        stopping = {"flag": False}

        def stop(signum, frame):
            stopping["flag"] = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.stdout.write("sms_worker started")
        while not stopping["flag"]:
            close_old_connections()
            try:
                fanned = dispatch_approved()
                sent = send_queued()
                if fanned["messages_queued"] or any(sent.values()):
                    self.stdout.write(f"dispatch {fanned} sms {sent}")
            except Exception:
                logger.exception("sms_worker cycle failed")
            if options["once"]:
                break
            time.sleep(options["interval"])
        self.stdout.write("sms_worker stopped")
