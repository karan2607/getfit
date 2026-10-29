"""
Convert `Exercise.superset_group` integers into `Composite` rows.

This MUTATES EXISTING ROWS, including live user plans, so the shape of it
matters more than the brevity:

* `rest_seconds` is taken from the LAST member, because that is exactly what
  the card rendered before this migration. The point is to preserve what the
  user currently sees, not to improve it -- any improvement belongs in a change
  the user can see coming.
* Groups with fewer than two members are SKIPPED, not converted. The server
  rejects them, but a hand-edited or legacy row should degrade to an ordinary
  exercise rather than become a one-member "superset".
* Members are grouped per (day, superset_group) and ordered by `order`, so two
  days reusing the integer 1 -- which is normal, the integer was only ever
  day-local -- do not merge into one container.
* SetLog is untouched. Each member keeps its own logged sets, its own field
  contract and its own history; this moves the grouping, not the training data.

Reverse rebuilds the integers from the containers, so the migration is not a
one-way door.
"""

from django.db import migrations


def rest_from_members(rest_values):
    """
    The group's rest: the LAST member's value that is set.

    Module level and pure so it can be tested directly. This is the one
    judgement call in the migration -- it is what the card rendered before the
    container existed, so preserving it preserves what the user currently sees.
    Choosing the max, or the first, would silently change every existing plan.
    """
    for value in reversed(list(rest_values)):
        if value is not None:
            return value
    return None


def forwards(apps, schema_editor):
    Exercise = apps.get_model('api', 'Exercise')
    Composite = apps.get_model('api', 'Composite')

    grouped = {}
    for exercise in (Exercise.objects
                     .filter(superset_group__isnull=False)
                     .order_by('day_id', 'superset_group', 'order', 'id')):
        grouped.setdefault(
            (exercise.day_id, exercise.superset_group), []).append(exercise)

    # Start composite ordering after any existing rows for the day, so a
    # re-run cannot collide.
    next_order = {}
    made = 0
    skipped = 0
    for (day_id, _group), members in sorted(
            grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        if len(members) < 2:
            skipped += 1
            continue
        order = next_order.get(day_id, 0)
        next_order[day_id] = order + 1

        # What the card showed before this migration: the last member's rest.
        rest = rest_from_members(m.rest_seconds for m in members)

        composite = Composite.objects.create(
            day_id=day_id, order=order, rest_seconds=rest)
        Exercise.objects.filter(pk__in=[m.pk for m in members]).update(
            composite=composite)
        made += 1

    if made or skipped:
        print('[0045] %d composite(s) created, %d lone group(s) left ungrouped'
              % (made, skipped))


def backwards(apps, schema_editor):
    """Rebuild the integers so this is not a one-way door."""
    Exercise = apps.get_model('api', 'Exercise')
    Composite = apps.get_model('api', 'Composite')

    counters = {}
    for composite in Composite.objects.all().order_by('day_id', 'order', 'id'):
        number = counters.get(composite.day_id, 0) + 1
        counters[composite.day_id] = number
        Exercise.objects.filter(composite=composite).update(
            superset_group=number)
    Composite.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0044_composite_container'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
