"""
"Today" from the user's point of view.

Why this exists
---------------
`TIME_ZONE = 'UTC'` and every "today" in the app was `timezone.now().date()`,
which is the SERVER's date. The two disagree for several hours a day, and the
app both writes and reads dates:

    meal written  date=2026-10-19   created_at=2026-10-20 05:52 UTC
    meal read     date=2026-10-20                       -> nothing

The client sent the correct local date on the write, so the data was right.
Every read of "today" then asked for the UTC date and found an empty day. At
11pm Eastern the health page reported 0 calories eaten against 5 logged meals,
and it had been doing so every night.

The server cannot infer a timezone from a request, so `UserProfile.timezone`
carries it and the client sets it from
`Intl.DateTimeFormat().resolvedOptions().timeZone`. Unset means UTC, which is
exactly today's behaviour -- so a profile that never reports one is unaffected.

Everything that means "the user's current day" must go through `user_today`.
"""

import datetime as dt
import logging

try:                                            # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:                             # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

from django.utils import timezone

logger = logging.getLogger(__name__)

# datetime.timezone.utc, NOT django.utils.timezone.utc.
#
# The Django alias was deprecated in 4.1 and REMOVED in 5.0, and
# requirements.txt pins only `django>=4.2`. The local venv resolved to 4.2.30
# where it still exists, production resolved to 5.x where it does not, so the
# first version of this module took out /api/health/series/ and
# /api/health/calorie-balance/ with an AttributeError while every local test
# passed. The stdlib constant is correct on every version.
UTC = dt.timezone.utc

DEFAULT_TIMEZONE = 'UTC'

# Resolved zones are cached: ZoneInfo reads from the filesystem, and these are
# looked up on nearly every health and diet request.
_ZONE_CACHE = {}


def resolve_zone(name):
    """
    A tzinfo for an IANA name, falling back to UTC.

    Never raises. A bad or unknown name must not break a request -- the old
    behaviour (server UTC) is the safe fallback, not a 500.
    """
    key = (name or '').strip() or DEFAULT_TIMEZONE
    if key in _ZONE_CACHE:
        return _ZONE_CACHE[key]
    zone = UTC
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            logger.warning('unknown timezone %r, falling back to UTC', name)
            zone = UTC
    _ZONE_CACHE[key] = zone
    return zone


def timezone_name(user):
    """The user's stored IANA timezone name, or 'UTC'."""
    profile = getattr(user, 'profile', None)
    return (getattr(profile, 'timezone', '') or DEFAULT_TIMEZONE).strip() \
        or DEFAULT_TIMEZONE


def user_now(user):
    """Timezone-aware now, in the user's own zone."""
    return timezone.now().astimezone(resolve_zone(timezone_name(user)))


def user_today(user):
    """
    The user's current calendar date.

    This is the one that matters: a meal logged at 11pm on the 19th belongs to
    the 19th, not to the 20th the server has already reached.
    """
    return user_now(user).date()
