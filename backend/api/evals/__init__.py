"""
Plan evaluation.

Scores an AI-generated workout plan against the rules the generation prompt
already states, so "is the model doing a good job?" becomes a number instead of
an argument.

Why this exists
---------------
Every quality defect in this app so far has been found by a human using it and
filing feedback: a rep range that contradicted the card, a sled push that asked
only for seconds, exercise names that fragmented a user's own history. The test
suite is 490+ deterministic unit tests on parsing and classification, and not one
of them looks at whether a *generated plan* is good programming.

Design rules
------------
* Every rule here corresponds to something the prompt already demands. This
  module does not invent coaching opinions; it checks stated contracts.
* Rules reuse the canonical implementations (`exercise_types`, the placeholder
  regex in `views`) rather than re-deriving them. A rule that disagreed with the
  server's own normalisation would be worse than no rule.
* Scoring is deterministic and offline. The golden set is checked in, so a score
  change always means a code change -- never model nondeterminism.
* `eval_plans --live` is the only path that calls the model, and it is manual.
  CI scores fixtures only.
"""

from .scorer import Finding, RuleResult, Scorecard, score_plan     # noqa: F401
