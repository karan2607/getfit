from django.db import migrations


def clear_stale_guides(apps, schema_editor):
    """
    Drop cached guides that still carry baked-in progression advice.

    Every personal ExerciseGuide in production was written in one 10-second
    window before the progression logic was fixed, and nothing regenerated them
    -- so users kept seeing advice like "Maintain 90.0lb" for a weight they had
    just failed at, days after the bug was corrected.

    Advice is no longer cached at all; it is computed per request from logged
    history. These rows are dropped so they regenerate with stable content only
    and pick up an advice_version stamp.

    This is a cache delete of regenerable rows, not a mutation of user data.
    """
    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')

    stale = []
    for guide in ExerciseGuide.objects.all().iterator():
        data = guide.data if isinstance(guide.data, dict) else {}
        has_baked_advice = any(
            data.get(field) for field in
            ('recommended_weight', 'recommended_target', 'coaching_note')
        )
        unstamped = (data.get('advice_version') or 0) < 2
        if has_baked_advice or unstamped:
            stale.append(guide.pk)

    if stale:
        print('[0032] clearing %d cached guide(s) with baked-in or unstamped advice'
              % len(stale))
        ExerciseGuide.objects.filter(pk__in=stale).delete()
    else:
        print('[0032] no stale cached guides')


def noop(apps, schema_editor):
    """Nothing to restore -- the deleted rows regenerate on demand."""


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0031_clear_lateral_raise_guides'),
    ]

    operations = [
        migrations.RunPython(clear_stale_guides, noop),
    ]
