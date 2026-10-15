import re

from django.db import migrations

# md5 hexdigest: exactly 32 lowercase hex characters.
_MD5_SHAPED = re.compile(r'^[0-9a-f]{32}$')


def replace_derivable_tokens(apps, schema_editor):
    """
    Replace sync tokens that were derived rather than generated.

    The old scheme was md5(user_id + "getFit"), with the algorithm and salt
    readable in a public repository -- so anyone who learned an account's UUID
    could compute its health-write credential, and there was no way to rotate it
    short of deleting all the user's health data.

    Only md5-SHAPED tokens are replaced. Any token that is already unguessable
    is left alone, so a working Apple Shortcut does not break for no reason.
    A replaced token DOES invalidate that user's Shortcut until they re-paste it.
    """
    from api.models import HealthConnection as RealModel

    HealthConnection = apps.get_model('api', 'HealthConnection')

    replaced = kept = 0
    for conn in HealthConnection.objects.all().iterator():
        token = conn.sync_token or ''
        if token and not _MD5_SHAPED.match(token):
            kept += 1
            continue
        conn.sync_token = RealModel.new_sync_token()
        conn.save(update_fields=['sync_token'])
        replaced += 1
        print('  [0034] replaced derivable sync token for user %s '
              '(their Shortcut needs the new token)' % conn.user_id)

    print('[0034] %d token(s) replaced, %d already unguessable' % (replaced, kept))


def noop(apps, schema_editor):
    """
    Irreversible by design: the point is that the old values were derivable.
    Reversing would mean recomputing them, which is the bug.
    """
    print('[0034] reverse is a no-op -- the old tokens were derivable by design')


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0033_alter_healthworkout_terra_workout_id_and_more'),
    ]

    operations = [
        migrations.RunPython(replace_derivable_tokens, noop),
    ]
