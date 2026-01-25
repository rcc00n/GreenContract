from pathlib import Path
import os
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in _allowed_hosts_env.split(",") if host.strip()]
_csrf_trusted_env = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in _csrf_trusted_env.split(",") if origin.strip()]
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO").upper()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rentals",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "car_rental.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "car_rental.wsgi:application"
ASGI_APPLICATION = "car_rental.asgi:application"


def _database_from_url(url: str | None):
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/") or "",
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": int(parsed.port or 5432),
    }


_db_from_url = _database_from_url(os.environ.get("DATABASE_URL"))
DATABASES = {
    "default": _db_from_url
    or {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "car_rental"),
        "USER": os.environ.get("POSTGRES_USER", "car_rental"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "car_rental"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": int(os.environ.get("POSTGRES_PORT", 5432)),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DATE_FORMAT = "d-m-Y"
DATETIME_FORMAT = "d-m-Y H:i"
TIME_FORMAT = "H:i"
DATE_INPUT_FORMATS = ["%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
DATETIME_INPUT_FORMATS = ["%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M"]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True
MEDIA_URL = os.environ.get("DJANGO_MEDIA_URL", "/media/")
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

OCR_STORE_UPLOADS = os.environ.get("OCR_STORE_UPLOADS", "true").lower() == "true"
OCR_UPLOAD_TTL_HOURS = int(os.environ.get("OCR_UPLOAD_TTL_HOURS", "72"))
OCR_DEBUG = os.environ.get("OCR_DEBUG", "false").lower() == "true"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "rentals:dashboard"
LOGOUT_REDIRECT_URL = "login"

# Idle session timeout: 30 minutes of inactivity.
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True

# Upload limits: allow larger CSV/XLS/XLSX imports by default.
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("FILE_UPLOAD_MAX_MEMORY_SIZE", 50 * 1024 * 1024))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", 50 * 1024 * 1024))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "gunicorn.error": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "gunicorn.access": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
