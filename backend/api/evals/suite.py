"""
Versioning for the rule set, so eval numbers stay comparable.

The problem this solves
-----------------------
The golden set is 8 **frozen** historical plans. They are immutable artifacts:
they cannot improve. So adding a rule can only ever lower a mean computed over
them, and that mean conflates two completely different things — "the generator
got worse" and "we now measure more".

In practice this meant the floor in `test_mean_score_floor` was lowered twice
(0.95 → 0.92 → 0.85), each time with the honest explanation "a new rule found
real defects". A threshold that gets moved whenever it fails is not a
regression test.

What replaces it
----------------
An exact per-fixture-per-rule snapshot (`fixtures/baseline.json`). Any change in
scoring is then a precise, reviewable diff rather than a number drifting
against a threshold.

`SUITE_VERSION` and `rules_fingerprint()` close the obvious hole: if someone
changes RULES without re-baselining, the fingerprint no longer matches and the
test says so explicitly, instead of the mean quietly sagging.

Bump SUITE_VERSION whenever RULES changes OR whenever the vocabulary a rule
judges against changes -- a new measurement type moves scores without touching
RULES at all, so the fingerprint alone would not notice. Re-baseline in its own
commit.
"""

import hashlib
import json

from .rules import RULES

# Bump when RULES changes (added/removed rule, or a changed weight), or when
# the vocabulary the rules judge against changes.
# 2: added the per-week generation rules (outline tiling, notes map,
# single-week payload). Each is neutral on the historical fixtures.
# 3: cardio_machine and weight_distance. RULES is untouched, so the fingerprint
# is unchanged -- but measurement_type_agrees now has two more types to compare
# against, and every fixture predates them, so their treadmills and carries
# claim the type that was correct when they were generated. The new findings
# are real and are what the reclassification step fixes in production.
# 4: added no_merged_exercise_names (weight 3.0). Neutral on all eight
# historical fixtures -- the reported merge lives in the live plan, not in a
# captured fixture -- so this re-baseline changes the fingerprint and nothing
# else. That is the ideal shape for a new rule: it proves the golden set was
# already clean on the defect rather than quietly absorbing it.
# 5: added goals_are_served (weight 2.0). Neutral on all eight fixtures --
# their contexts predate `specific_goals` -- so this re-baseline is the
# fingerprint plus one null key per plan, and no existing score moves.
SUITE_VERSION = 5


def rules_fingerprint(rules=RULES):
    """
    A stable hash of the rule set's shape: names and weights, in order.

    Deliberately does NOT hash rule bodies -- tightening a rule's logic SHOULD
    show up as a baseline diff you look at, not as an opaque fingerprint
    mismatch that tells you nothing about what changed.
    """
    payload = json.dumps(
        [[name, float(weight)] for name, _func, weight in rules],
        sort_keys=False, separators=(',', ':'),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_baseline(cards, suite_version=SUITE_VERSION):
    """
    The snapshot structure: failure counts per fixture per rule.

    Counts rather than scores, because a count is what a finding actually is --
    scores are derived and would make a one-finding change look like a float
    comparison problem.
    """
    return {
        'suite_version': suite_version,
        'rules_fingerprint': rules_fingerprint(),
        '_comment': (
            'Exact expected failure counts per fixture per rule. Regenerate '
            'with `manage.py eval_plans --update-baseline`, always as its own '
            'commit -- bundling it with a code change would launder a '
            'regression into the baseline.'
        ),
        'plans': {
            card.label: {
                result.rule: result.failed for result in card.results
            }
            for card in sorted(cards, key=lambda c: c.label)
        },
    }


def diff_baseline(baseline, cards):
    """
    Compare a fresh run against the stored snapshot.

    Returns a list of human-readable differences, empty when identical.
    Reports fingerprint/version drift first, since that explains the rest.
    """
    problems = []

    stored_fingerprint = baseline.get('rules_fingerprint')
    current = rules_fingerprint()
    if stored_fingerprint != current:
        problems.append(
            'RULES changed but the baseline was not regenerated '
            '(suite_version=%s, fingerprint %s… != %s…). Bump SUITE_VERSION '
            'and run `manage.py eval_plans --update-baseline` as its own '
            'commit.' % (baseline.get('suite_version'),
                         str(stored_fingerprint)[:8], current[:8]))

    if baseline.get('suite_version') != SUITE_VERSION:
        problems.append(
            'suite_version is %s in the baseline but %s in code.'
            % (baseline.get('suite_version'), SUITE_VERSION))

    stored_plans = baseline.get('plans') or {}
    fresh = {card.label: card for card in cards}

    for label in sorted(set(stored_plans) | set(fresh)):
        if label not in fresh:
            problems.append('%s: in the baseline but not scored' % label)
            continue
        if label not in stored_plans:
            problems.append('%s: scored but missing from the baseline' % label)
            continue
        expected = stored_plans[label]
        actual = {r.rule: r.failed for r in fresh[label].results}
        for rule in sorted(set(expected) | set(actual)):
            want = expected.get(rule)
            got = actual.get(rule)
            if want != got:
                problems.append('%s / %s: baseline %s, now %s'
                                % (label, rule, want, got))
    return problems
