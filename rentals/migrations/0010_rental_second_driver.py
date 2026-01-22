from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0009_customer_new_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="rental",
            name="second_driver",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="secondary_rentals",
                to="rentals.customer",
                verbose_name="Второй водитель",
                help_text="Опциональный второй водитель для договора.",
            ),
        ),
    ]
