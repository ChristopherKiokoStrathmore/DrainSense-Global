"""Request ids: every response carries X-Request-ID and every error body repeats it."""
import contextvars
import logging
import re
import uuid

_request_id = contextvars.ContextVar("request_id", default="-")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def current_request_id():
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response


class RequestIdLogFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id.get()
        return True
