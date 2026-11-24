from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Renumbered from 0057: the chat-context migration took that
        # number on staging while this one sat unpushed, and two leaves
        # stop the app from starting at all.
        ('api', '0057_chatsession_context'),
    ]

    operations = [
        migrations.AddField(
            model_name='setlog',
            name='target_reps',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
