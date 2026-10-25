from django.db import migrations

# Exercises whose cached guide holds the WRONG illustration.
#
# `dead bug` was aliased to `superman`, overriding its own exact KB entry, and
# a backward treadmill walk fuzzy-matched forward walking. Both were reported
# from the feedback queue. The matcher is fixed, but `ExerciseGuide.data`
# already has the bad image URLs baked in, so the rows must be cleared to be
# regenerated.
#
# Deleting a cached guide is not data loss -- steps/muscles/tips/images are all
# regenerable, and advice is computed per request and never stored. Same
# pattern as 0029, 0031 and 0032.
WRONG = (
    'dead bug',
    'bird dog',
    'bird-dog',
    'birddog',
    'backward walking',
    'backward treadmill',
)


def clear(apps, schema_editor):
    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')
    matched = []
    for guide in ExerciseGuide.objects.all().only('id', 'name'):
        lowered = (guide.name or '').lower()
        if any(fragment in lowered for fragment in WRONG):
            matched.append(guide.id)
    deleted = ExerciseGuide.objects.filter(id__in=matched).delete()[0]
    print('[0040] cleared %d cached guide(s) with wrong images' % deleted)


def noop(apps, schema_editor):
    """Irreversible by design: the rows regenerate on next view."""


class Migration(migrations.Migration):
    dependencies = [('api', '0039_apikey_feedback_scope')]
    operations = [migrations.RunPython(clear, noop)]
