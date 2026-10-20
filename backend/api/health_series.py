"""
Time-bucketed health series.

One implementation shared by the REST endpoint, the UI charts and the MCP tool,
so a chart and an agent can never disagree about what "last 6 months" means.

Aggregation choice: every metric is reported as a **per-day average within the
bucket**, not a bucket total. That keeps the y-axis comparable when switching
granularity -- a weekly step total is ~7x a daily one, so totals would make the
chart jump by an order of magnitude on every toggle and hide the actual trend.
Each bucket also carries `days_with_data` so a partial week is obvious rather
than looking like a slump.
"""

from collections import OrderedDict
from datetime import timedelta

from django.utils import timezone

from .models import HealthDailySummary
from .user_time import user_today

METRICS = (
    'steps',
    'active_calories',
    'resting_calories',
    'resting_heart_rate',
    'sleep_hours',
)

# range key -> (bucket granularity, how far back, label format)
RANGES = {
    'daily': {'bucket': 'day', 'days': 14, 'label': '%-d %b'},
    'weekly': {'bucket': 'week', 'days': 84, 'label': '%-d %b'},
    'monthly': {'bucket': 'month', 'days': 183, 'label': '%b'},
}

DEFAULT_RANGE = 'daily'


def _bucket_start(day, granularity):
    if granularity == 'day':
        return day
    if granularity == 'week':
        return day - timedelta(days=day.weekday())      # Monday
    return day.replace(day=1)                            # month


def _next_bucket(start, granularity):
    if granularity == 'day':
        return start + timedelta(days=1)
    if granularity == 'week':
        return start + timedelta(days=7)
    # First of the following month, without dateutil.
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def build_series(user, range_key=DEFAULT_RANGE, today=None):
    """
    Bucketed averages for one user.

    Returns a dict with the resolved range, the bucket list oldest-first, and a
    `summary` carrying the latest value and the change against the previous
    bucket for each metric.

    Buckets with no data are still present with null values, so a gap in syncing
    reads as a gap rather than silently compressing the x-axis.
    """
    spec = RANGES.get(range_key) or RANGES[DEFAULT_RANGE]
    resolved = range_key if range_key in RANGES else DEFAULT_RANGE
    granularity = spec['bucket']

    # The user's current day, not the server's -- otherwise the newest bucket
    # is tomorrow for anyone west of UTC in the evening.
    today = today or user_today(user)
    window_start = _bucket_start(today - timedelta(days=spec['days'] - 1),
                                 granularity)

    rows = (HealthDailySummary.objects
            .filter(user=user, date__gte=window_start, date__lte=today)
            .order_by('date')
            .values('date', *METRICS))

    # Pre-create every bucket so gaps are visible.
    buckets = OrderedDict()
    cursor = window_start
    while cursor <= today:
        buckets[cursor] = {m: [] for m in METRICS}
        cursor = _next_bucket(cursor, granularity)

    for row in rows:
        key = _bucket_start(row['date'], granularity)
        if key not in buckets:
            continue
        for metric in METRICS:
            value = row.get(metric)
            if value is not None:
                buckets[key][metric].append(value)

    out = []
    for start, collected in buckets.items():
        end = _next_bucket(start, granularity) - timedelta(days=1)
        point = {
            'start': start.isoformat(),
            'end': min(end, today).isoformat(),
            'label': start.strftime(spec['label']).lstrip('0'),
            'days_with_data': max(
                (len(v) for v in collected.values()), default=0),
        }
        for metric in METRICS:
            values = collected[metric]
            point[metric] = round(sum(values) / len(values), 1) if values else None
        out.append(point)

    return {
        'range': resolved,
        'bucket': granularity,
        'from': window_start.isoformat(),
        'to': today.isoformat(),
        'buckets': out,
        'summary': _summarise(out),
        'has_data': any(b['days_with_data'] for b in out),
    }


def _summarise(buckets):
    """
    Latest value per metric plus the change against the previous bucket that
    had data, so the UI can show a trend without re-deriving it (and so an
    agent gets the same number the chart shows).
    """
    summary = {}
    for metric in METRICS:
        points = [(b['label'], b[metric]) for b in buckets
                  if b[metric] is not None]
        if not points:
            summary[metric] = {'latest': None, 'previous': None,
                               'change': None, 'change_pct': None}
            continue
        latest = points[-1][1]
        previous = points[-2][1] if len(points) > 1 else None
        change = round(latest - previous, 1) if previous is not None else None
        change_pct = (round((latest - previous) / previous * 100, 1)
                      if previous not in (None, 0) else None)
        summary[metric] = {
            'latest': latest, 'previous': previous,
            'change': change, 'change_pct': change_pct,
            'points': len(points),
        }
    return summary
