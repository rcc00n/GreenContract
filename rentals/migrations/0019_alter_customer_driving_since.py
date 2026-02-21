from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0018_customer_driving_since_year_only"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="driving_since",
            field=models.DateField(
                blank=True,
                help_text="Дата начала стажа вождения (ГГГГ; можно указать полную дату).",
                null=True,
                verbose_name="Стаж с",
            ),
        ),
    ]
