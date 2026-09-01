#!/bin/sh

set -u

: "${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY is required}"
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${SC05_SIMULATOR_DJANGO_SECRET_KEY:?SC05_SIMULATOR_DJANGO_SECRET_KEY is required}"
: "${SC05_SIMULATOR_USERNAME:?SC05_SIMULATOR_USERNAME is required}"
: "${SC05_SIMULATOR_PASSWORD:?SC05_SIMULATOR_PASSWORD is required}"

simulator_port=8000
ready_file=/tmp/sheepcontabil-worker-ready
simulator_pid=""
worker_pid=""
shutdown_requested=0

stop_children() {
    trap - EXIT INT TERM
    rm -f "$ready_file"
    if [ -n "$worker_pid" ]; then
        kill "$worker_pid" 2>/dev/null || true
    fi
    if [ -n "$simulator_pid" ]; then
        kill "$simulator_pid" 2>/dev/null || true
    fi
    if [ -n "$worker_pid" ]; then
        wait "$worker_pid" 2>/dev/null || true
    fi
    if [ -n "$simulator_pid" ]; then
        wait "$simulator_pid" 2>/dev/null || true
    fi
}

request_shutdown() {
    if [ "$shutdown_requested" -eq 1 ]; then
        return
    fi
    shutdown_requested=1
    rm -f "$ready_file"
    if [ -n "$worker_pid" ]; then
        # Celery receives TERM first and performs a warm shutdown while the
        # simulator remains available to the in-flight RPA operation.
        kill -TERM "$worker_pid" 2>/dev/null || true
    else
        exit 0
    fi
}

finish_worker_shutdown() {
    worker_exit_code=0
    wait "$worker_pid" || worker_exit_code=$?
    exit "$worker_exit_code"
}

trap stop_children EXIT
trap request_shutdown INT TERM

# Web owns migrations. Independent Railway deploys can start the worker first,
# so wait for the shared schema instead of running migrations concurrently.
migration_attempt=0
until DJANGO_SETTINGS_MODULE=config.settings.production python src/manage.py migrate --check >/dev/null 2>&1; do
    migration_attempt=$((migration_attempt + 1))
    if [ "$migration_attempt" -ge 45 ]; then
        echo "Database migrations did not become ready within 90 seconds." >&2
        exit 1
    fi
    sleep 2
done

rm -f "$ready_file"

# The simulator subprocess inherits only the variables it needs; Redis, S3 and
# OpenAI are omitted. This reduces accidental exposure but is not a strong
# security boundary because both processes share one Railway container.
env -i \
    PATH="$PATH" \
    PYTHONPATH="${PYTHONPATH:-/app/src}" \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.simulator \
    DJANGO_SECRET_KEY="$SC05_SIMULATOR_DJANGO_SECRET_KEY" \
    DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost \
    APP_TIME_ZONE="${APP_TIME_ZONE:-America/Sao_Paulo}" \
    DATABASE_URL="$DATABASE_URL" \
    SC05_SIMULATOR_USERNAME="$SC05_SIMULATOR_USERNAME" \
    SC05_SIMULATOR_PASSWORD="$SC05_SIMULATOR_PASSWORD" \
    SC05_SIMULATOR_READY_FILE="$ready_file" \
    gunicorn config.simulator_wsgi:application \
        --chdir src \
        --bind "0.0.0.0:${simulator_port}" \
        --workers 1 \
        --threads 2 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - &
simulator_pid=$!

live=0
attempt=0
while [ "$attempt" -lt 20 ]; do
    if ! kill -0 "$simulator_pid" 2>/dev/null; then
        wait "$simulator_pid" || true
        echo "SC-05 simulator stopped during startup." >&2
        exit 1
    fi
    if curl --fail --silent --connect-timeout 1 --max-time 2 \
        "http://127.0.0.1:${simulator_port}/health/live" >/dev/null; then
        live=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ "$live" -ne 1 ]; then
    echo "SC-05 simulator did not become live during startup." >&2
    exit 1
fi

celery --app config worker --loglevel INFO --concurrency 1 &
worker_pid=$!

# Avoid advertising readiness if Celery fails immediately after startup.
attempt=0
while [ "$attempt" -lt 3 ]; do
    if ! kill -0 "$worker_pid" 2>/dev/null; then
        if [ "$shutdown_requested" -eq 1 ]; then
            finish_worker_shutdown
        fi
        wait "$worker_pid" || true
        echo "Celery worker stopped during startup." >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ "$shutdown_requested" -eq 1 ]; then
    finish_worker_shutdown
fi

touch "$ready_file"

ready=0
attempt=0
while [ "$attempt" -lt 30 ]; do
    if [ "$shutdown_requested" -eq 1 ]; then
        finish_worker_shutdown
    fi
    if curl --fail --silent --connect-timeout 1 --max-time 2 \
        "http://127.0.0.1:${simulator_port}/health/ready" >/dev/null; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    echo "Worker and simulator did not become ready during startup." >&2
    exit 1
fi

# POSIX sh has no portable wait -n. Poll both children and fail the service as
# soon as either the Celery worker or its private simulator exits.
while kill -0 "$simulator_pid" 2>/dev/null && kill -0 "$worker_pid" 2>/dev/null; do
    sleep 2
done

if ! kill -0 "$worker_pid" 2>/dev/null; then
    if [ "$shutdown_requested" -eq 1 ]; then
        finish_worker_shutdown
    fi
    wait "$worker_pid" || true
    echo "Celery worker stopped unexpectedly; terminating the private simulator." >&2
else
    wait "$simulator_pid" || true
    echo "SC-05 simulator stopped; terminating the Celery worker." >&2
fi

exit 1
