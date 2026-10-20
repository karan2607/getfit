"""
Notes must not carry a static progression rule.

Reported as "Getting different advice in diff places", with a screenshot of one
card showing both of these at once:

    notes:  "Increase reps from last week. Add 2.5-5lb when all sets
             completed with good form for the target reps in a given week."
    advice: "Only 10 reps at 85.0lb last session (target 15) -> DECREASE
             WEIGHT. Previously hit 15+ reps at 55.0lb -> go back to about
             55.0lb"

The user was right: the same card told them to add weight and to drop 30lb.

The prompt has always forbidden this ("no 'Start ...', no 'Add ... when all
sets clean', no placeholder weights of any kind") because weight
recommendations are recomputed per request from logged history. But only
PLACEHOLDER weights were stripped on write, so a rule with concrete numbers
sailed through. Prohibitions in prose do not hold; the gate does.
"""

from django.test import SimpleTestCase, TestCase

from api import views
from api.models import User, WorkoutPlan, WorkoutDay


class CleanNotesTests(SimpleTestCase):
    def assert_cleans(self, before, after):
        self.assertEqual(views._clean_exercise_notes(before), after)

    # -- the reported case ---------------------------------------------------

    def test_the_reported_note_is_removed_entirely(self):
        self.assert_cleans(
            'Increase reps from last week. Add 2.5-5lb when all sets completed '
            'with good form for the target reps in a given week.',
            '')

    def test_a_form_cue_survives_alongside_a_stripped_rule(self):
        self.assert_cleans(
            'Pause 1s at the chest. Add 5lb when all sets are clean.',
            'Pause 1s at the chest.')

    # -- weights in notes ----------------------------------------------------

    def test_any_concrete_weight_is_removed(self):
        for note in ('Add 5lb when clean.', 'Start at 95 lbs.',
                     'Increase by 2.5kg weekly.', 'Use 20 pounds.',
                     'Work up to 2.5-5lb heavier.'):
            self.assertEqual(views._clean_exercise_notes(note), '', msg=note)

    def test_placeholder_weights_are_still_removed(self):
        """The original gate must keep working."""
        self.assert_cleans('Focus on form. Start Xlb.', 'Focus on form.')
        self.assert_cleans('Start Ykg. Keep elbows tucked.', 'Keep elbows tucked.')

    # -- week-over-week instructions ----------------------------------------

    def test_progression_instructions_are_removed(self):
        for note in ('Increase reps from last week.',
                     'Add a rep each week.',
                     'Go heavier next week.',
                     'Once you can complete all sets, progress.',
                     'When all sets are completed, move up.'):
            self.assertEqual(views._clean_exercise_notes(note), '', msg=note)

    # -- what must NOT be removed -------------------------------------------

    def test_genuine_form_cues_survive(self):
        for note in (
            'Pause 1s at the chest. Keep the elbows at 45 degrees.',
            'Drive through the heel and keep the chest up.',
            'Swapped from barbell squat — knee pain. Keep depth pain-free.',
            'Arm circles 20x, band pull-aparts 15x, shoulder CARs 5x each.',
            'Control the eccentric for 3 seconds.',
            'Squeeze at the top for a full second.',
            'Keep the weight close to your body throughout.',
        ):
            self.assertEqual(views._clean_exercise_notes(note), note, msg=note)

    def test_a_tempo_in_seconds_is_not_mistaken_for_a_weight(self):
        note = 'Lower for 3 seconds, pause 1 second, drive up.'
        self.assertEqual(views._clean_exercise_notes(note), note)

    def test_rep_counts_in_a_warmup_list_survive(self):
        """Warmup notes are a drill list and must come through intact."""
        note = ('Leg swings 10x each, hip circles 10x, bodyweight squats 10x, '
                'ankle rolls 30s')
        self.assertEqual(views._clean_exercise_notes(note), note)

    def test_set_notation_survives(self):
        note = 'Work up to 3 x 12 with a controlled tempo.'
        self.assertEqual(views._clean_exercise_notes(note), note)

    def test_blank_input_is_handled(self):
        for value in ('', None):
            self.assertEqual(views._clean_exercise_notes(value), '')


class CreationAppliesTheGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='notes@test.local', password='x')
        plan = WorkoutPlan.objects.create(user=self.user, title='P', current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')

    def test_a_generated_exercise_has_the_rule_stripped(self):
        exercise = views._create_exercise(self.day, {
            'name': 'Dumbbell Lateral Raises', 'sets': 3, 'reps': '15-20',
            'notes': ('Increase reps from last week. Add 2.5-5lb when all sets '
                      'completed with good form.'),
        }, 0)
        self.assertEqual(exercise.notes, '')

    def test_a_generated_exercise_keeps_its_form_cue(self):
        exercise = views._create_exercise(self.day, {
            'name': 'Barbell Bench Press', 'sets': 3, 'reps': '6-8',
            'notes': 'Pause 1s at the chest. Add 5lb when all sets are clean.',
        }, 0)
        self.assertEqual(exercise.notes, 'Pause 1s at the chest.')
