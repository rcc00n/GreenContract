from django.contrib import admin

from .models import Car, ContractTemplate, Customer, Rental


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    list_display = (
        "plate_number",
        "make",
        "model",
        "year",
        "rate_1_4_high",
        "rate_5_14_high",
        "rate_15_plus_high",
        "is_active",
    )
    search_fields = ("plate_number", "make", "model")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "phone", "license_number")
    search_fields = ("full_name", "email", "phone", "license_number")


@admin.register(Rental)
class RentalAdmin(admin.ModelAdmin):
    list_display = ("id", "car", "customer", "start_date", "end_date", "total_price", "status")
    list_filter = ("status", "start_date", "end_date")
    search_fields = ("car__plate_number", "customer__full_name")


@admin.register(ContractTemplate)
class ContractTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "format")
