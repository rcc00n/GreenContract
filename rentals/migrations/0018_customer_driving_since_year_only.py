from django.db import migrations, models


def set_driving_since_year_only(apps, schema_editor):
    Customer = apps.get_model("rentals", "Customer")
    Customer.objects.filter(
        driving_since__isnull=False,
        driving_since__month=1,
        driving_since__day=1,
    ).update(driving_since_year_only=True)


def unset_driving_since_year_only(apps, schema_editor):
    Customer = apps.get_model("rentals", "Customer")
    Customer.objects.update(driving_since_year_only=False)


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0017_rental_created_via_wizard"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="driving_since_year_only",
            field=models.BooleanField(
                default=False,
                help_text="Если включено, стаж отображается только годом.",
                verbose_name="Стаж только год",
            ),
        ),
        migrations.RunPython(set_driving_since_year_only, unset_driving_since_year_only),
    ]
