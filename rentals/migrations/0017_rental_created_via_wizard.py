from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0016_alter_contracttemplate_placeholder_help"),
    ]

    operations = [
        migrations.AddField(
            model_name="rental",
            name="created_via_wizard",
            field=models.BooleanField(
                default=False,
                help_text="Отметка, что договор сформирован через мастер.",
                verbose_name="Создано мастером",
            ),
        ),
    ]
