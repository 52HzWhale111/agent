#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
test -f .env || { echo 'Copy .env.example to .env and fill deployment values first.' >&2; exit 1; }
compose() { docker compose --env-file .env -f "${ZHIGOU_COMPOSE_FILE:-infra/docker-compose.yml}" "$@"; }
compose config --quiet
compose --profile build build sandbox-execution
compose up -d --build
compose ps
