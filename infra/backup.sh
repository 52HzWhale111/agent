#!/bin/sh
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
compose() { docker compose --env-file .env -f infra/docker-compose.yml "$@"; }
tmp="backups/postgres-$stamp.sql.gz.tmp"
# Keep pg_dump separate: a pipeline could hide its failure under POSIX sh.
sql="backups/postgres-$stamp.sql.tmp"
trap 'rm -f "$tmp" "$sql"' EXIT HUP INT TERM
compose exec -T db pg_dump -U zhigou -d zhigou > "$sql"
test -s "$sql"
gzip -c "$sql" > "$tmp"
gzip -t "$tmp"
mv "$tmp" "backups/postgres-$stamp.sql.gz"
rm -f "$sql"
# SQLite online backup includes committed WAL contents; uploads are immutable after creation.
compose exec -T gateway python -c 'import sqlite3; s=sqlite3.connect("/data/gateway.sqlite3"); d=sqlite3.connect("/data/gateway-backup.sqlite3"); s.backup(d); d.close(); s.close()'
container=$(compose ps -q gateway)
docker cp "$container:/data/gateway-backup.sqlite3" "backups/gateway-$stamp.sqlite3"
mkdir -p "backups/uploads-$stamp"
docker cp "$container:/data/uploads/." "backups/uploads-$stamp/"
find backups -type f -name 'postgres-*.sql.gz' -mtime +7 -delete
find backups -type f -name 'gateway-*.sqlite3' -mtime +7 -delete
# Upload snapshots are retained until manually reviewed; never auto-delete student files.
printf 'Backup complete: %s\n' "$stamp"
