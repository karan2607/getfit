"""
Drop `Exercise.superset_group`, now that 0045 has moved every group into a
`Composite` row.

Separate from 0044 deliberately: 0045 has to READ this column to build the
containers, so removing it in the same migration would have left every
existing group silently ungrouped.

Note the AI WIRE FORMAT still uses `superset_group` -- a flat integer is what
the model handles reliably, and nesting is where it previously failed. The
translation to a Composite happens on save. This removal is about storage only.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0045_migrate_superset_groups'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exercise',
            name='superset_group',
        ),
    ]
