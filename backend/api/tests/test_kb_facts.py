"""
Versioned facts: nothing is overwritten, nothing is deleted.

Why supersession rather than UPDATE
-----------------------------------
The broad note "avoid deadlifts and overhead press" was narrowed in
conversation to "no deadlifts from the floor, no STANDING overhead press --
machine and seated are fine". Both versions were true at different times, and
with a mutable field there is no way to see which one a given week was
generated under, or that the rule ever changed.

So a correction creates a NEW row and points the old one at it. The chain is
the audit trail: a constraint that silently broadens again is visible as a
diff rather than invisible as an overwrite.

`effective_date` is when the fact became TRUE, not when it was recorded. A
rule refined on 26 Oct and written down on 2 Nov is dated the 26th, because
that is the date that decides which plans it should have governed.
"""

from datetime import date

from django.test import TestCase

from api.models import KbRule, User, UserFact


class UserFactSupersessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='fact@test.local', password='x')

    def fact(self, **kwargs):
        kwargs.setdefault('user', self.user)
        kwargs.setdefault('key', 'back_constraint')
        kwargs.setdefault('value', {'rule': 'avoid deadlifts and overhead press'})
        kwargs.setdefault('effective_date', date(2026, 9, 1))
        return UserFact.objects.create(**kwargs)

    def test_a_new_fact_is_current(self):
        self.assertTrue(self.fact().is_current)

    def test_superseding_leaves_the_old_row_intact(self):
        old = self.fact()
        new = old.supersede_with(
            value={'rule': 'no deadlifts from the floor; seated OHP is fine'},
            effective_date=date(2026, 10, 26))

        old.refresh_from_db()
        self.assertEqual(old.value, {'rule': 'avoid deadlifts and overhead press'})
        self.assertEqual(old.superseded_by_id, new.id)
        self.assertFalse(old.is_current)
        self.assertTrue(new.is_current)
        self.assertEqual(UserFact.objects.count(), 2)

    def test_the_replacement_inherits_key_and_source(self):
        old = self.fact(source='onboarding')
        new = old.supersede_with(value={'rule': 'narrower'},
                                 effective_date=date(2026, 10, 26))
        self.assertEqual(new.key, 'back_constraint')
        self.assertEqual(new.source, 'onboarding')

    def test_the_replacement_can_override_the_source(self):
        old = self.fact(source='onboarding')
        new = old.supersede_with(value={'rule': 'narrower'},
                                 source='user, chat 2026-10-26',
                                 effective_date=date(2026, 10, 26))
        self.assertEqual(new.source, 'user, chat 2026-10-26')

    def test_a_row_cannot_be_superseded_twice(self):
        """
        Two successors would leave two rows both claiming to be current, and
        no way to say which the plan generator should read.
        """
        old = self.fact()
        old.supersede_with(value={'rule': 'a'}, effective_date=date(2026, 10, 1))
        with self.assertRaises(ValueError) as ctx:
            old.supersede_with(value={'rule': 'b'}, effective_date=date(2026, 10, 2))
        self.assertIn('already superseded', str(ctx.exception))

    def test_the_chain_is_walkable_in_both_directions(self):
        v1 = self.fact()
        v2 = v1.supersede_with(value={'rule': 'v2'}, effective_date=date(2026, 10, 1))
        v3 = v2.supersede_with(value={'rule': 'v3'}, effective_date=date(2026, 10, 30))

        self.assertEqual(v1.superseded_by.superseded_by, v3)
        self.assertEqual(v3.supersedes.get(), v2)
        self.assertEqual(
            [f.value['rule'] for f in UserFact.objects.filter(superseded_by__isnull=True)],
            ['v3'])

    def test_effective_date_is_when_it_became_true_not_when_written(self):
        """
        Ordering is by effective_date, so a fact recorded late still sorts to
        the day it actually started applying.
        """
        self.fact(key='a', effective_date=date(2026, 10, 26))
        self.fact(key='b', effective_date=date(2026, 10, 30))
        self.assertEqual(
            [f.key for f in UserFact.objects.all()], ['b', 'a'])

    def test_facts_are_scoped_to_their_user(self):
        other = User.objects.create_user(email='other@test.local', password='x')
        self.fact()
        UserFact.objects.create(
            user=other, key='back_constraint', value={'rule': 'theirs'},
            effective_date=date(2026, 10, 1))
        self.assertEqual(self.user.facts.count(), 1)
        self.assertEqual(other.facts.count(), 1)


class KbRuleTests(TestCase):
    """Operating rules get the same treatment, minus the user."""

    def test_rules_supersede_the_same_way(self):
        rule = KbRule.objects.create(
            key='meal_confirm_before_log',
            value={'rule': 'confirm any guessed estimate before logging'},
            effective_date=date(2026, 10, 1))
        new = rule.supersede_with(
            value={'rule': 'confirm every estimate, guessed or not'},
            effective_date=date(2026, 11, 1))

        rule.refresh_from_db()
        self.assertFalse(rule.is_current)
        self.assertTrue(new.is_current)
        self.assertEqual(KbRule.objects.count(), 2)
