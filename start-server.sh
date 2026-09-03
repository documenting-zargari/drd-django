#!/usr/bin/env bash
# start-server.sh
echo "Hello from DRD Django"
python manage.py collectstatic --no-input
python manage.py migrate --no-input
# Provision the ArangoSearch analyzers/views the search + concordance endpoints
# need (concord / concord_fold tokenisers). Idempotent — a no-op once done.
python manage.py ensure_search_views || echo "ensure_search_views failed (non-fatal) — whole-word search / concordance / word list will 400 until it runs"

gunicorn roma.wsgi --user www-data --bind 0.0.0.0:8010 --workers 3 & nginx -g "daemon off;"
