"""
Bearer authentication for long-lived integration keys.

    Authorization: Bearer <43-char key>

Kept separate from DRF's `TokenAuthentication` (`Authorization: Token …`) on
purpose. That one is the browser session credential: there is exactly one row
per user and `logout()` deletes it, so it is the wrong thing to hand to an
agent. See `ApiKey` for the full story.

Scoping
-------
A key carries a scope, and `scope_allows()` is the single source of truth for
what it may reach.

It is enforced inside `authenticate()`, NOT only as a DRF permission class,
and that is deliberate: nearly every view in this codebase (55 decorators
across 54 views) sets `@permission_classes([IsAuthenticated])`, which **overrides**
`DEFAULT_PERMISSION_CLASSES`. A permission class alone would therefore be dead
code for essentially every endpoint. Checking at authentication time cannot be
bypassed by a view forgetting to opt in.

The default `logging` scope is deliberately unable to escalate: it cannot
change the password, mint reset links, reach the admin endpoints, or delete a
plan. Anything not explicitly allowed is denied, so a new endpoint is closed
until someone decides otherwise.

`readonly` is narrower still: safe methods only, so it cannot write anything at
all. It exists so an unattended agent can VERIFY a claim about stored data
instead of trusting the text it was handed. Note that a key only ever reaches
its own owner's rows -- every view filters on `request.user` -- so a scope
widens which endpoints are reachable, never whose data.
"""

import logging

from django.utils.translation import gettext_lazy as _
from rest_framework import authentication, exceptions, permissions

from .models import ApiKey

logger = logging.getLogger(__name__)

KEYWORD = 'Bearer'


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate `Authorization: Bearer <key>` against ApiKey."""

    keyword = KEYWORD

    def authenticate(self, request):
        result = self._authenticate(request)
        if result is None:
            return None
        user, api_key = result
        # Authorization at authentication time -- see the module docstring on
        # why a permission class cannot carry this.
        if not scope_allows(api_key, request.path, request.method):
            logger.warning('api key %s… (scope=%s) denied %s %s',
                           api_key.prefix, api_key.scope,
                           request.method, request.path)
            raise exceptions.PermissionDenied(
                'This API key is scoped to %s and cannot use this endpoint.'
                % api_key.get_scope_display())
        return result

    def _authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None                     # let another authenticator try
        if len(header) == 1:
            raise exceptions.AuthenticationFailed(
                _('Invalid Bearer header. No credentials provided.'))
        if len(header) > 2:
            raise exceptions.AuthenticationFailed(
                _('Invalid Bearer header. Key should not contain spaces.'))

        try:
            key = header[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed(
                _('Invalid Bearer header. Key contains invalid characters.'))

        return self.authenticate_credentials(key)

    def authenticate_credentials(self, key):
        try:
            api_key = ApiKey.objects.select_related('user').get(key=key)
        except ApiKey.DoesNotExist:
            # Same message for unknown and revoked, so the response cannot be
            # used to tell "never existed" from "was revoked".
            raise exceptions.AuthenticationFailed(_('Invalid API key.'))

        if not api_key.is_active:
            raise exceptions.AuthenticationFailed(_('Invalid API key.'))
        if not api_key.user.is_active:
            raise exceptions.AuthenticationFailed(_('User inactive.'))

        api_key.touch()
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return self.keyword


# Path prefixes a `logging`-scoped key may reach, matched against
# request.path after /api/. Allow-list, not deny-list: a newly added endpoint
# is unreachable by an integration key until it is listed here on purpose.
LOGGING_SCOPE_ALLOWED = (
    'health/',
    'diet/',
    'workouts/',
    'mcp/',
    'profile/',          # GET only, see below
)

# Even inside the allowed prefixes, these are closed to a logging key: they
# either escalate privilege or destroy data.
LOGGING_SCOPE_DENIED = (
    'workouts/plans/generate/',
    'diet/plans/generate/',
)

# A `feedback` key may reach ONLY this. It is for the autonomous queue runner.
FEEDBACK_SCOPE_ALLOWED = ('feedback/',)

# A `readonly` key may READ these and nothing else.
#
# Why this scope exists: the feedback runner escalated an item in order to
# protect logged sets the user had ALREADY corrected. All it had was the
# submission text and a feedback-scoped key, so it could not check whether the
# claim about the data was still true -- it had to trust a stale description.
#
# Narrower than `logging` on purpose. `logging` can WRITE health, diet and
# workout data; this cannot write anything, which is the whole point of handing
# it to an unattended agent.
READONLY_SCOPE_ALLOWED = (
    'health/',
    'workouts/',
    'sessions/',
    'diet/',
    'profile/',
)

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


def _tail(path):
    """The part of the path after /api/, so prefixes read naturally."""
    marker = '/api/'
    return path[path.index(marker) + len(marker):] if marker in path else path


def scope_allows(api_key, path, method):
    """
    May this key reach `method path`? The single source of truth.

    Allow-list by design: an endpoint added tomorrow is closed to integration
    keys until someone lists it on purpose.
    """
    if api_key is None or not isinstance(api_key, ApiKey):
        return True                         # not an integration key
    if api_key.scope == ApiKey.SCOPE_FULL:
        return True

    tail = _tail(path)

    # The feedback runner: queue access and nothing else. Note this is still
    # gated by the view's own superuser check, so the scope widens nothing on
    # its own.
    if api_key.scope == ApiKey.SCOPE_FEEDBACK:
        return any(tail.startswith(prefix)
                   for prefix in FEEDBACK_SCOPE_ALLOWED)

    # Read-only: safe methods, allow-listed prefixes, nothing else. Both
    # conditions are checked here rather than relying on the views, because
    # `logging` already proved that a method rule living in one branch is easy
    # to miss.
    if api_key.scope == ApiKey.SCOPE_READONLY:
        if method not in SAFE_METHODS:
            return False
        return any(tail.startswith(prefix)
                   for prefix in READONLY_SCOPE_ALLOWED)

    if any(tail.startswith(denied) for denied in LOGGING_SCOPE_DENIED):
        return False
    if not any(tail.startswith(prefix) for prefix in LOGGING_SCOPE_ALLOWED):
        return False
    # The profile is readable (an agent wants the unit and timezone) but not
    # writable by a logging key.
    if tail.startswith('profile/') and method not in SAFE_METHODS:
        return False
    # Deleting a plan is destructive and not something a logging key needs.
    if method == 'DELETE' and tail.startswith('workouts/plans/'):
        return False
    return True


class ApiKeyScopePermission(permissions.BasePermission):
    """
    Defense in depth over the same `scope_allows`.

    Registered in DEFAULT_PERMISSION_CLASSES, but note that almost every view
    here overrides permission_classes, so this does NOT run for most
    endpoints. The authoritative check is in ApiKeyAuthentication.
    """

    message = 'This API key is not permitted to use this endpoint.'

    def has_permission(self, request, view):
        return scope_allows(request.auth, request.path, request.method)
