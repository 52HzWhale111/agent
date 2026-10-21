#!/bin/sh
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
compose() { docker compose --env-file .env -f "${ZHIGOU_COMPOSE_FILE:-infra/docker-compose.yml}" "$@"; }
partial="backups/.partial-$stamp"
mkdir "$partial"
tmp="$partial/postgres.sql.gz.tmp"
# Keep pg_dump separate: a pipeline could hide its failure under POSIX sh.
sql="$partial/postgres.sql.tmp"
trap 'rm -f "$tmp" "$sql"' EXIT HUP INT TERM
compose exec -T db pg_dump -U zhigou -d zhigou > "$sql"
test -s "$sql"
gzip -c "$sql" > "$tmp"
gzip -t "$tmp"
mv "$tmp" "$partial/postgres.sql.gz"
rm -f "$sql"
# Snapshot includes SQLite WAL contents, registered uploads and SHA-256 checksums.
snapshot="/data/backup-$stamp"
compose exec -T gateway python /app/infra/gateway_backup.py backup /data "$snapshot"
container=$(compose ps -q gateway)
docker cp "$container:$snapshot" "$partial/gateway"
backend_container=$(compose ps -q backend)
docker cp "$backend_container:/service/backend/storage" "$partial/backend-uploads"
(cd "$partial" && find backend-uploads -type f -exec sha256sum {} \; > backend-uploads.sha256)
if [ -s "$partial/backend-uploads.sha256" ]; then
    (cd "$partial" && sha256sum -c backend-uploads.sha256)
fi
image=$(docker inspect --format '{{.Image}}' "$container")
docker run --rm --user 0 --mount "type=bind,source=$PWD/$partial/gateway,target=/snapshot,readonly" --entrypoint python "$image" /app/infra/gateway_backup.py verify /snapshot
mv "$partial" "backups/snapshot-$stamp"
# Remove only this script's verified staging copy, after publishing the host backup.
compose exec -T gateway python -c 'import shutil,sys; shutil.rmtree(sys.argv[1])' "$snapshot"
# Do not automatically delete student data; review retention and off-host copies in deploy.md.
printf 'Backup complete: %s\n' "$stamp"
