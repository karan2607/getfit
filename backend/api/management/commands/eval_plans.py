"""
Score generated workout plans against the quality rules.

    manage.py eval_plans                      # score the checked-in golden set
    manage.py eval_plans --json               # machine-readable
    manage.py eval_plans --rule=session_volume
    manage.py eval_plans --live --model=gemini-2.5-flash --n=3

`--live` is the ONLY path that calls the model, and it is manual on purpose: it
costs money, it is nondeterministic, and CI must never depend on a third party
being up. Without it the command is fully offline.
"""

import glob
import json
import os

from django.core.management.base import BaseCommand, CommandError

from api.evals import score_plan
from api.evals.rules import RULES
from api.evals.scorer import format_scorecard

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'evals', 'fixtures')


def load_fixtures(pattern='*.json'):
    out = []
    for path in sorted(glob.glob(os.path.join(FIXTURE_DIR, pattern))):
        with open(path) as handle:
            doc = json.load(handle)
        out.append((
            doc.get('label') or os.path.basename(path),
            doc.get('plan') or {},
            doc.get('context') or {},
        ))
    return out


class Command(BaseCommand):
    help = 'Score AI-generated workout plans against the quality rules.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true',
                            help='emit JSON instead of a report')
        parser.add_argument('--rule', action='append', dest='rules',
                            help='only run this rule (repeatable)')
        parser.add_argument('--quiet', action='store_true',
                            help='scores only, no individual findings')
        parser.add_argument('--fixture', default='*.json',
                            help='glob for which fixtures to score')
        parser.add_argument('--live', action='store_true',
                            help='generate fresh plans from the model')
        parser.add_argument('--model', default=None,
                            help='model id for --live (repeatable via commas)')
        parser.add_argument('--temperature', type=float, default=None)
        parser.add_argument('--n', type=int, default=1,
                            help='--live: plans to generate per model')

    def handle(self, *args, **options):
        rules = RULES
        if options.get('rules'):
            wanted = set(options['rules'])
            unknown = wanted - {name for name, _, _ in RULES}
            if unknown:
                raise CommandError('unknown rule(s): %s' % ', '.join(sorted(unknown)))
            rules = tuple(r for r in RULES if r[0] in wanted)

        if options['live']:
            cases = self._generate_live(options)
        else:
            cases = load_fixtures(options['fixture'])
            if not cases:
                raise CommandError('no fixtures matched %r in %s'
                                   % (options['fixture'], FIXTURE_DIR))

        cards = [score_plan(plan, context, label=label, rules=rules)
                 for label, plan, context in cases]

        if options['json']:
            self.stdout.write(json.dumps(
                {'plans': [c.as_dict() for c in cards],
                 'mean_score': _mean([c.score for c in cards])}, indent=2))
            return

        for card in cards:
            self.stdout.write(format_scorecard(card, verbose=not options['quiet']))
            self.stdout.write('')

        self._summary(cards, rules)

    # -- reporting -----------------------------------------------------------

    def _summary(self, cards, rules):
        self.stdout.write('=' * 60)
        self.stdout.write('SUMMARY (%d plan(s))' % len(cards))
        self.stdout.write('=' * 60)
        for card in cards:
            score = card.score
            self.stdout.write('  %-12s %s   %d finding(s)' % (
                card.label, 'n/a' if score is None else '%.3f' % score,
                len(card.findings)))

        mean = _mean([c.score for c in cards])
        self.stdout.write('')
        self.stdout.write('  mean score: %s'
                          % ('n/a' if mean is None else '%.3f' % mean))

        # Which rule is costing the most across the whole set -- the thing
        # worth fixing next.
        self.stdout.write('')
        self.stdout.write('  failures by rule:')
        totals = {}
        for card in cards:
            for result in card.results:
                checked, failed = totals.get(result.rule, (0, 0))
                totals[result.rule] = (checked + result.checked,
                                       failed + result.failed)
        for name, _, _ in rules:
            checked, failed = totals.get(name, (0, 0))
            if not checked:
                self.stdout.write('    %-36s      — not applicable' % name)
            else:
                self.stdout.write('    %-36s %4d/%-5d failed' % (
                    name, failed, checked))

    # -- live generation -----------------------------------------------------

    def _generate_live(self, options):
        """
        Generate fresh plans straight from the model.

        Deliberately bypasses the DB: this scores what the MODEL produced, not
        what the server corrected on write. Server-side normalisation is what
        hides model defects, so an eval that scored post-normalisation output
        would report everything as fine.
        """
        from api import views
        from api.gemini import call_gemini_json, GeminiError

        models = [m.strip() for m in (options['model'] or '').split(',') if m.strip()]
        if not models:
            models = [None]          # module default

        context = {
            'goal': 'build_muscle', 'level': 'intermediate',
            'duration_weeks': 8,
            'injuries': 'Mild lower back discomfort on heavy spinal loading.',
        }
        system, user = views._workout_generate_prompt(
            None, 5, 8,
            fitness_goal='build_muscle', experience_level='intermediate',
            equipment='full gym', notes=context['injuries'],
            body_context='', preferred_unit='lb',
            specific_goals=['build_muscle'], goal_params={},
            confirmed_target=None, past_exercises=[],
        )

        cases = []
        for model in models:
            for attempt in range(1, options['n'] + 1):
                label = '%s#%d' % (model or 'default', attempt)
                self.stderr.write('generating %s ...' % label)
                try:
                    kwargs = {'system_prompt': system, 'user_prompt': user}
                    if model:
                        kwargs['model'] = model
                    if options['temperature'] is not None:
                        kwargs['temperature'] = options['temperature']
                    plan = call_gemini_json(**kwargs)
                except (GeminiError, TypeError) as exc:
                    self.stderr.write('  FAILED: %s' % exc)
                    continue
                cases.append((label, plan, context))
        if not cases:
            raise CommandError('no plans were generated')
        return cases


def _mean(values):
    real = [v for v in values if v is not None]
    if not real:
        return None
    return sum(real) / len(real)
