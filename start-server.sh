#!/usr/bin/env bash
# start-server.sh
echo "Hello from DRD Django"
python manage.py collectstatic --no-input
python manage.py migrate --no-input

gunicorn roma.wsgi --user www-data --bind 0.0.0.0:8010 --workers 3 & nginx -g "daemon off;"