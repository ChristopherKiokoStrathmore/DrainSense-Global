"""Bearer-token auth (API_CONTRACT §2.0).

Two kinds of caller hold tokens, both configured in the environment:
- Louisa's USSD service: USSD_SERVICE_TOKEN, scope "ussd" (only /ussd/* endpoints).
- APEX demo controls, scheduler, router, tests: NGAO_API_TOKENS (comma list), scope "api".
"""
import hmac

from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import BasePermission

SCOPE_API = "api"
SCOPE_USSD = "ussd"


class ServicePrincipal:
    """The authenticated caller. There are no user accounts; officers sign in to APEX, not here."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, name, scope):
        self.name = name
        self.scope = scope

    def __str__(self):
        return self.name


def _known_tokens():
    if settings.NGAO_USSD_SERVICE_TOKEN:
        yield settings.NGAO_USSD_SERVICE_TOKEN, ServicePrincipal("ussd-service", SCOPE_USSD)
    for index, token in enumerate(settings.NGAO_API_TOKENS):
        yield token, ServicePrincipal(f"api-client-{index + 1}", SCOPE_API)


class BearerTokenAuthentication(BaseAuthentication):
    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Authorization header must be 'Bearer <token>'")
        presented = parts[1].decode("utf-8", errors="replace")
        for token, principal in _known_tokens():
            if hmac.compare_digest(presented.encode(), token.encode()):
                return principal, token
        raise exceptions.AuthenticationFailed("Invalid token")

    def authenticate_header(self, request):
        return "Bearer"


class _ScopePermission(BasePermission):
    scopes = ()
    message = "This token may not call this endpoint"

    def has_permission(self, request, view):
        principal = request.user
        if principal is None or not getattr(principal, "is_authenticated", False):
            raise exceptions.NotAuthenticated()
        return principal.scope in self.scopes


class IsApiClient(_ScopePermission):
    scopes = (SCOPE_API,)


class IsUssdService(_ScopePermission):
    """Louisa's service; API clients may also call these (demo controls, tests)."""

    scopes = (SCOPE_USSD, SCOPE_API)
