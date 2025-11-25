from django.urls import path

from . import views

app_name = "rentals"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cars/", views.CarListView.as_view(), name="car_list"),
    path("cars/new/", views.CarCreateView.as_view(), name="car_create"),
    path("cars/<int:pk>/edit/", views.CarUpdateView.as_view(), name="car_update"),
    path("cars/export/", views.export_cars_csv, name="export_cars_csv"),
    path("cars/import/", views.import_cars_csv, name="import_cars_csv"),
    path("customers/", views.CustomerListView.as_view(), name="customer_list"),
    path("customers/new/", views.CustomerCreateView.as_view(), name="customer_create"),
    path("customers/<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="customer_update"),
    path("customers/export/", views.export_customers_csv, name="export_customers_csv"),
    path("customers/import/", views.import_customers_csv, name="import_customers_csv"),
    path("rentals/", views.RentalListView.as_view(), name="rental_list"),
    path("rentals/new/", views.RentalCreateView.as_view(), name="rental_create"),
    path("rentals/<int:pk>/edit/", views.RentalUpdateView.as_view(), name="rental_update"),
    path("rentals/export/", views.export_rentals_csv, name="export_rentals_csv"),
    path("rentals/import/", views.import_rentals_csv, name="import_rentals_csv"),
    path(
        "rentals/<int:rental_id>/contract/<int:template_id>/",
        views.generate_contract,
        name="generate_contract",
    ),
    path(
        "contract-templates/",
        views.ContractTemplateListView.as_view(),
        name="contract_template_list",
    ),
    path(
        "contract-templates/new/",
        views.ContractTemplateCreateView.as_view(),
        name="contract_template_create",
    ),
    path(
        "contract-templates/<int:pk>/edit/",
        views.ContractTemplateUpdateView.as_view(),
        name="contract_template_update",
    ),
]
