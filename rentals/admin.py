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
    search_fields = ("plate_number", "make", "model", "vin", "sts_number")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "birth_date", "phone", "email", "license_number", "discount_percent")
    search_fields = ("full_name", "email", "phone", "license_number", "registration_address", "license_issued_by")


@admin.register(Rental)
class RentalAdmin(admin.ModelAdmin):
    list_display = (
        "contract_number",
        "car",
        "customer",
        "start_date",
        "end_date",
        "total_price",
        "prepayment",
        "balance_due",
        "status",
    )
    list_filter = ("status", "start_date", "end_date")
    search_fields = ("contract_number", "car__plate_number", "car__make", "car__model", "customer__full_name")


@admin.register(ContractTemplate)
class ContractTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "format")
