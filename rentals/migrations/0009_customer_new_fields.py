from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rentals", "0008_car_extra_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="birth_date",
            field=models.DateField(blank=True, null=True, help_text="Дата рождения."),
        ),
        migrations.AddField(
            model_name="customer",
            name="license_issued_by",
            field=models.CharField(
                max_length=255,
                blank=True,
                null=True,
                help_text="Кем выдано водительское удостоверение.",
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="driving_since",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Дата начала стажа вождения (Стаж с ...).",
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="discount_percent",
            field=models.DecimalField(
                max_digits=5,
                decimal_places=2,
                blank=True,
                null=True,
                help_text="Персональная скидка, %.",
            ),
        ),
    ]
