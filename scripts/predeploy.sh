#!/bin/sh

set -eu

python src/manage.py migrate --noinput

if [ "${SEED_DEMO_ON_DEPLOY:-false}" = "true" ]; then
    python src/manage.py seed_demo
fi
