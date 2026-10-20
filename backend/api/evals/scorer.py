"""
Turn a generated plan into a scorecard.

Scoring model
-------------
Each rule reports how many things it checked and what was wrong. A rule's score
is the fraction of its checks that passed, and the overall score is the weighted
mean across rules that actually applied.

Rules that checked nothing are EXCLUDED from the mean rather than counted as
perfect. A plan for an uninjured user should not score higher just because the
injury rule had nothing to look at -- that would make the overall number go up
as coverage goes down, which is the opposite of useful.
"""

from collections import namedtuple

from .rules import RULES

Finding = namedtuple('Finding', 'rule where detail')


class RuleResult(object):
    __slots__ = ('rule', 'weight', 'checked', 'findings')

    def __init__(self, rule, weight, checked, findings):
        self.rule = rule
        self.weight = weight
        self.checked = checked
        self.findings = findings

    @property
    def applicable(self):
        return self.checked > 0

    @property
    def failed(self):
        return len(self.findings)

    @property
    def score(self):
        """1.0 when every check passed; None when the rule did not apply."""
        if not self.applicable:
            return None
        # Findings can exceed checks only if a rule reports several problems for
        # one item; clamp so a rule can never drag the mean below zero.
        return max(0.0, 1.0 - float(self.failed) / float(self.checked))

    def __repr__(self):
        return '<RuleResult %s %s/%s>' % (
            self.rule, self.checked - self.failed, self.checked)


class Scorecard(object):
    __slots__ = ('label', 'results', 'error')

    def __init__(self, label, results, error=None):
        self.label = label
        self.results = results
        self.error = error

    @property
    def score(self):
        """Weighted mean over applicable rules; None if nothing applied."""
        pairs = [(r.weight, r.score) for r in self.results if r.applicable]
        if not pairs:
            return None
        total_weight = sum(w for w, _ in pairs)
        if not total_weight:
            return None
        return sum(w * s for w, s in pairs) / total_weight

    @property
    def findings(self):
        out = []
        for result in self.results:
            out.extend(result.findings)
        return out

    @property
    def passed(self):
        return not self.findings and self.error is None

    def by_rule(self):
        return {r.rule: r for r in self.results}

    def as_dict(self):
        return {
            'label': self.label,
            'score': self.score,
            'error': self.error,
            'rules': [
                {'rule': r.rule, 'weight': r.weight, 'checked': r.checked,
                 'failed': r.failed, 'score': r.score,
                 'findings': [{'where': f.where, 'detail': f.detail}
                              for f in r.findings]}
                for r in self.results
            ],
        }


def score_plan(plan, context=None, label='plan', rules=RULES):
    """
    Score one plan. `context` carries what the plan was ASKED for
    (`injuries`, `goal`, `level`, `duration_weeks`), which some rules need in
    order to judge the output against the request.

    A rule that raises is recorded as a finding against itself rather than
    taking the whole run down -- this scores live model output, where a missing
    key is exactly the kind of thing worth reporting.
    """
    context = context or {}
    results = []
    for name, func, weight in rules:
        findings = []

        def find(where_, detail, _rule=name, _findings=findings):
            _findings.append(Finding(_rule, where_, detail))

        try:
            checked = func(plan, context, find)
        except Exception as exc:                        # noqa: BLE001
            results.append(RuleResult(
                name, weight, 1,
                [Finding(name, '<rule>', 'rule raised %s: %s'
                         % (type(exc).__name__, exc))]))
            continue
        results.append(RuleResult(name, weight, checked or 0, findings))
    return Scorecard(label, results)


def format_scorecard(card, verbose=True):
    """Human-readable report for the management command."""
    lines = []
    score = card.score
    header = '%s  score=%s' % (
        card.label, 'n/a' if score is None else '%.3f' % score)
    lines.append(header)
    lines.append('-' * max(len(header), 40))
    if card.error:
        lines.append('ERROR: %s' % card.error)
        return '\n'.join(lines)

    for result in card.results:
        if not result.applicable:
            lines.append('  %-36s      —  (not applicable)' % result.rule)
            continue
        lines.append('  %-36s %5.1f%%  %d/%d checks%s' % (
            result.rule, (result.score or 0) * 100,
            result.checked - result.failed, result.checked,
            '' if not result.failed else '   <-- %d FAILED' % result.failed))
    if verbose and card.findings:
        lines.append('')
        lines.append('Findings:')
        for finding in card.findings:
            lines.append('  [%s] %s: %s'
                         % (finding.rule, finding.where, finding.detail))
    return '\n'.join(lines)
