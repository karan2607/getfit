"""
A suggestion has to land on a weight the equipment can actually make.

Reported 26 Nov: "Most of these do not have an option to increase by 2.5 and
should not be suggested. Instead for those, it should be increase of 5. And
for ones we can attach 2.5 lbs, or dumbbells, sure."

The engine rounded every suggestion to 2.5 lb and, when a factor was too
small to move a plate, nudged by exactly one 2.5 lb step. On a selectorised
machine or a cable stack that is not a weight you can select, so the advice
was unfollowable -- and it is the number a person is standing in front of
the machine reading.
"""
from django.test import SimpleTestCase

from api import weights


class IncrementTests(SimpleTestCase):
    def step(self, name, unit='lb'):
        return weights.increment_for(name, unit)

    def test_a_machine_moves_in_fives(self):
        """A selectorised stack has no 2.5 lb pin position."""
        self.assertEqual(self.step('Machine Chest Press'), 5.0)
        self.assertEqual(self.step('Leg Press'), 5.0)

    def test_a_cable_stack_moves_in_fives(self):
        self.assertEqual(self.step('Lat Pulldown'), 5.0)
        self.assertEqual(self.step('Seated Cable Rows'), 5.0)

    def test_dumbbells_may_move_by_two_and_a_half(self):
        """The owner's words: "or dumbbells, sure"."""
        self.assertEqual(self.step('Flat Dumbbell Press'), 2.5)
        self.assertEqual(self.step('Dumbbell Hammer Curl'), 2.5)

    def test_a_barbell_may_move_by_two_and_a_half(self):
        """Small plates attach to a bar; that is the "can attach" case."""
        self.assertEqual(self.step('Barbell Squat'), 2.5)
        self.assertEqual(self.step('Barbell Curl'), 2.5)

    def test_an_unknown_exercise_takes_the_cautious_step(self):
        """
        Five, not 2.5. A suggestion that cannot be selected is worse than
        one that is slightly coarse.
        """
        self.assertEqual(self.step('Some Machine Nobody Has Heard Of'), 5.0)

    def test_kilograms_halve_the_same_way(self):
        self.assertEqual(self.step('Machine Chest Press', 'kg'), 2.5)
        self.assertEqual(self.step('Flat Dumbbell Press', 'kg'), 1.25)


class RoundingTests(SimpleTestCase):
    def test_a_machine_suggestion_lands_on_a_five(self):
        for raw in (101.0, 102.4, 103.7, 97.6):
            got = weights.round_to_increment(raw, 'lb', 'Machine Chest Press')
            self.assertEqual(got % 5, 0, '%s -> %s' % (raw, got))

    def test_a_dumbbell_suggestion_may_land_on_a_half_step(self):
        # 51.3 is nearer 52.5 than 50; a machine would have to pick 50.
        self.assertEqual(
            weights.round_to_increment(51.3, 'lb', 'Flat Dumbbell Press'),
            52.5)
        self.assertEqual(
            weights.round_to_increment(51.3, 'lb', 'Machine Chest Press'),
            50.0)

    def test_rounding_without_a_name_keeps_the_old_behaviour(self):
        """Callers that have no exercise still get the fine increment."""
        self.assertEqual(weights.round_to_increment(51.3, 'lb'), 52.5)
