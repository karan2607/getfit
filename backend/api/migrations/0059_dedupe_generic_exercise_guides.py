"""
Make the shared exercise guides actually unique, after clearing the rows
that prove they were not.

`unique_together = ('user', 'name')` never constrained them: the shared rows
carry user=NULL, Postgres treats NULLs as distinct, so two rows for one
exercise were always legal. `update_or_create(user=None, name=...)` then
raised MultipleObjectsReturned and the guide endpoint answered 503. Found on
staging, where "Incline Dumbbell Press" looked like a missing photo and was
really a crash before the photo was ever looked up.

Deduping keeps the NEWEST row per name, matched case-insensitively because
every read is `name__iexact` while every write passes the exact string. The
rows are a cache of generated content, re-created on the next read, so
discarding the older copy loses nothing a user can see.
"""
from django.db import migrations, models
from django.db.models.functions import Lower


def dedupe(apps, schema_editor):
    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')
    seen, doomed = {}, []
    rows = (ExerciseGuide.objects.filter(user__isnull=True)
            .order_by('-created_at', '-id')
            .values_list('id', 'name'))
    for pk, name in rows:
        key = (name or '').strip().lower()
        if key in seen:
            doomed.append(pk)
        else:
            seen[key] = pk
    if doomed:
        ExerciseGuide.objects.filter(pk__in=doomed).delete()
    print('[0059] kept %d generic guide(s), removed %d duplicate(s)'
          % (len(seen), len(doomed)))


def noop(apps, schema_editor):
    """Nothing to undo: the rows are a cache and were already redundant."""


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0058_setlog_target_reps'),
    ]

    operations = [
        migrations.RunPython(dedupe, noop),
        migrations.AddConstraint(
            model_name='exerciseguide',
            constraint=models.UniqueConstraint(
                Lower('name'),
                condition=models.Q(('user__isnull', True)),
                name='uniq_generic_exercise_guide_name'),
        ),
    ]
