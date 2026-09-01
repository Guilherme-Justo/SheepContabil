from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sc05_simulator", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="simulatorclient",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="ativo no sistema de tarefas"),
        ),
    ]
