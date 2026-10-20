"""
Guards against APIs that differ between the Django/Python version used locally
and the one running in production.

Why this file exists
--------------------
`requirements.txt` pins only `django>=4.2`, and nothing pins Python. The local
venv is Python 3.9, which caps Django at 4.2.30. Production is Python **3.14**,
which resolves Django to 5.x. So the two environments run different Django
majors, and a removed API is invisible locally.

That is not hypothetical. `api/user_time.py` shipped using
`django.utils.timezone.utc` -- deprecated in Django 4.1, **removed in 5.0**.
Every local test passed on 4.2.30 and production returned AttributeError on
/api/health/series/ and /api/health/calorie-balance/, which is what the user
saw as "Health page is not loading at all".

A behavioural test cannot catch this, because on the local version the
behaviour is correct. So the check is static: no module may reference an API
that does not exist in the version production runs.
"""

import ast
import os
import pathlib
import unittest

from django.test import SimpleTestCase

API_DIR = pathlib.Path(__file__).resolve().parent.parent


def python_files():
    for root, _dirs, files in os.walk(API_DIR):
        if 'migrations' in root or '__pycache__' in root:
            continue
        for name in files:
            if name.endswith('.py'):
                yield pathlib.Path(root) / name


# attribute path -> why it must not be used.
#
# Each entry is something that exists in Django 4.2 (so local tests would pass)
# and is gone in 5.x (so production breaks).
REMOVED_DJANGO_ATTRS = {
    ('django.utils.timezone', 'utc'):
        'removed in Django 5.0 — use datetime.timezone.utc',
    ('django.utils.encoding', 'force_text'):
        'removed in Django 4.0 — use force_str',
    ('django.utils.translation', 'ugettext'):
        'removed in Django 4.0 — use gettext',
    ('django.utils.translation', 'ugettext_lazy'):
        'removed in Django 4.0 — use gettext_lazy',
}


class RemovedDjangoApiTests(SimpleTestCase):
    """
    Catches `timezone.utc` and friends by reading the source, so the result
    does not depend on which Django the test run happens to have installed.
    """

    def test_no_module_uses_an_api_removed_in_a_later_django(self):
        offenders = []
        for path in python_files():
            if path.name == os.path.basename(__file__):
                continue          # this file names them on purpose
            source = path.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:   # pragma: no cover
                continue

            # Which local names refer to the modules we care about.
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        aliases[alias.asname or alias.name.split('.')[0]] = alias.name
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        full = '%s.%s' % (node.module, alias.name)
                        aliases[alias.asname or alias.name] = full

            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if not isinstance(node.value, ast.Name):
                    continue
                module = aliases.get(node.value.id)
                if not module:
                    continue
                reason = REMOVED_DJANGO_ATTRS.get((module, node.attr))
                if reason:
                    offenders.append('%s:%d  %s.%s — %s' % (
                        path.relative_to(API_DIR.parent), node.lineno,
                        node.value.id, node.attr, reason))

        self.assertEqual(offenders, [], msg='\n' + '\n'.join(offenders))

    def test_the_check_actually_detects_the_pattern(self):
        """
        A guard that cannot fail is worthless, so prove the detector fires on
        the exact code that caused the outage.
        """
        source = ('from django.utils import timezone\n'
                  'zone = timezone.utc\n')
        tree = ast.parse(source)
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = '%s.%s' % (
                        node.module, alias.name)
        found = [
            (aliases.get(node.value.id), node.attr)
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
        ]
        self.assertIn(('django.utils.timezone', 'utc'), found)
        self.assertIn(('django.utils.timezone', 'utc'), REMOVED_DJANGO_ATTRS)


class UserTimeRuntimeTests(SimpleTestCase):
    """The module that broke, exercised without relying on Django's aliases."""

    def test_resolve_zone_returns_a_usable_tzinfo_for_utc(self):
        import datetime as dt
        from api.user_time import resolve_zone

        zone = resolve_zone('UTC')
        moment = dt.datetime(2026, 10, 20, 5, 52, tzinfo=dt.timezone.utc)
        self.assertEqual(moment.astimezone(zone).date(), dt.date(2026, 10, 20))

    def test_the_utc_fallback_is_the_stdlib_constant(self):
        import datetime as dt
        from api import user_time

        self.assertIs(user_time.UTC, dt.timezone.utc)

    def test_an_unknown_zone_still_produces_a_working_tzinfo(self):
        """The path that raised AttributeError in production."""
        import datetime as dt
        from api.user_time import resolve_zone

        zone = resolve_zone('Not/AZone')
        moment = dt.datetime(2026, 10, 20, 5, 52, tzinfo=dt.timezone.utc)
        self.assertEqual(moment.astimezone(zone).date(), dt.date(2026, 10, 20))


if __name__ == '__main__':   # pragma: no cover
    unittest.main()
