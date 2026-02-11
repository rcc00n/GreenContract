#!/usr/bin/env sh
set -e

PORT="${PORT:-8000}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"
GRACE="${GUNICORN_GRACEFUL_TIMEOUT:-120}"
LOG_LEVEL="${GUNICORN_LOG_LEVEL:-info}"

# Apply database migrations automatically on startup so schema stays in sync.
python manage.py migrate --noinput

# Collect static files for production reverse proxies (Caddy/Nginx).
python manage.py collectstatic --noinput

exec gunicorn car_rental.wsgi:application \
  --bind "0.0.0.0:${PORT}" \
  --timeout "${TIMEOUT}" \
  --graceful-timeout "${GRACE}" \
  --access-logfile - \
  --error-logfile - \
  --log-level "${LOG_LEVEL}"
