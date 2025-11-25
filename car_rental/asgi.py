"""ASGI config for car_rental project."""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "car_rental.settings")

application = get_asgi_application()
