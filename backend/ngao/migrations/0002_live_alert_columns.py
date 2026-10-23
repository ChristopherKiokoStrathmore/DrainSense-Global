"""SQLite-only: match the live ALERTS / MESSAGES columns.

Live ALERTS.SUBSCRIBER_ID and SCORE_ID are NOT NULL, BODY is VARCHAR2(160), and MESSAGES has
no alert_id. `manage.py migrate` is never run against NgaoDB; this rebuilds the local test schema.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ngao", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="message",
            name="alert",
        ),
        migrations.AlterField(
            model_name="alert",
            name="body",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AlterField(
            model_name="alert",
            name="score",
            field=models.ForeignKey(
                db_column="score_id",
                on_delete=django.db.models.deletion.DO_NOTHING,
                to="ngao.riskscore",
            ),
        ),
        migrations.AlterField(
            model_name="alert",
            name="subscriber",
            field=models.ForeignKey(
                db_column="subscriber_id",
                on_delete=django.db.models.deletion.DO_NOTHING,
                to="ngao.subscriber",
            ),
        ),
    ]
