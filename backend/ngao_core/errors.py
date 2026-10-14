"""The one error body every endpoint returns (API_CONTRACT §2.1).

{ "error": { "code": "VALIDATION_ERROR", "message": "ward_id is required",
             "details": {"field": "ward_id"}, "request_id": "8f2c..." } }
"""
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, JsonResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from ngao_core.middleware import current_request_id

logger = logging.getLogger(__name__)


class ApiError(exceptions.APIException):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "INTERNAL"

    def __init__(self, message, details=None):
        super().__init__(detail=message)
        self.message = message
        self.details = details or {}


class InvalidInput(ApiError):
    """A business-rule validation failure (unknown ward, consent missing, ...)."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "VALIDATION_ERROR"

    def __init__(self, message, field=None, **details):
        if field:
            details["field"] = field
        super().__init__(message, details)


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"


class ConflictError(ApiError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "CONFLICT"


class UpstreamError(ApiError):
    """Wasiliana or OCI Generative AI failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "UPSTREAM_ERROR"


_CODES = [
    (exceptions.ValidationError, "VALIDATION_ERROR"),
    (exceptions.ParseError, "VALIDATION_ERROR"),
    (exceptions.NotAuthenticated, "UNAUTHORIZED"),
    (exceptions.AuthenticationFailed, "UNAUTHORIZED"),
    (exceptions.PermissionDenied, "FORBIDDEN"),
    (exceptions.NotFound, "NOT_FOUND"),
    (exceptions.MethodNotAllowed, "METHOD_NOT_ALLOWED"),
    (exceptions.UnsupportedMediaType, "VALIDATION_ERROR"),
]


def error_body(code, message, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": current_request_id(),
        }
    }


def _first_validation_error(detail, path=""):
    """Walk DRF's nested error detail down to the first (field, ErrorDetail)."""
    if isinstance(detail, dict):
        for key, value in detail.items():
            child = key if key == "non_field_errors" else (f"{path}.{key}" if path else key)
            return _first_validation_error(value, child)
    if isinstance(detail, list) and detail:
        for index, item in enumerate(detail):
            if isinstance(item, (dict, list)):
                return _first_validation_error(item, f"{path}[{index}]")
            return path, item
    return path, detail


def _validation_message(detail):
    field, error = _first_validation_error(detail)
    code = getattr(error, "code", None)
    if field in ("", "non_field_errors"):
        return str(error), {}
    if code == "required":
        return f"{field} is required", {"field": field}
    return f"{field}: {error}", {"field": field}


def exception_handler(exc, context):
    if isinstance(exc, (Http404, ObjectDoesNotExist)):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.messages)

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("unhandled error", exc_info=exc)
        return Response(error_body("INTERNAL", "Internal error"), status=500)

    if isinstance(exc, ApiError):
        body = error_body(exc.error_code, exc.message, exc.details)
    elif isinstance(exc, exceptions.ValidationError):
        message, details = _validation_message(exc.detail)
        details["errors"] = exc.detail
        body = error_body("VALIDATION_ERROR", message, details)
    else:
        code = next((c for cls, c in _CODES if isinstance(exc, cls)), "INTERNAL")
        detail = exc.detail if isinstance(exc, exceptions.APIException) else str(exc)
        body = error_body(code, str(detail))
    response.data = body
    return response


def json_404(request, exception=None):
    return JsonResponse(error_body("NOT_FOUND", "Not found"), status=404)


def json_500(request):
    return JsonResponse(error_body("INTERNAL", "Internal error"), status=500)
