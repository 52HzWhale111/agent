"""Bounded HTTPS pull of encrypted backups; credentials are read from a private local JSON file."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

import httpx

from infra.backup_export import NAME
from infra.gateway_backup import fingerprint

MONTHLY_BYTES = 1024 ** 3
MAX_FILE_BYTES = 64 * 1024 ** 2
MAX_INDEX_BYTES = 64 * 1024


class BackupSyncError(ValueError):
    """Only our fixed, non-sensitive error messages may appear in task status files."""


def reserve(directory, size):
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    with sqlite3.connect(directory / 'transfer-budget.sqlite3', timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS bandwidth (month TEXT PRIMARY KEY, reserved INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        used = db.execute('SELECT reserved FROM bandwidth WHERE month=?', (month,)).fetchone()
        used = used[0] if used else 0
        if used + size > MONTHLY_BYTES:
            raise BackupSyncError('Monthly encrypted-backup transfer limit reached')
        db.execute('INSERT INTO bandwidth VALUES (?,?) ON CONFLICT(month) DO UPDATE SET reserved=excluded.reserved', (month, used + size))


def download(client, name, limit, output):
    digest, size = hashlib.sha256(), 0
    with client.stream('GET', name) as response:
        response.raise_for_status()  # Redirects are refused; Authorization never follows another origin.
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise BackupSyncError('Download exceeded its declared size')
            output.write(chunk)
            digest.update(chunk)
    return {'size': size, 'sha256': digest.hexdigest()}


def sync(config, transport=None):
    base = httpx.URL(config['base_url'])
    if (base.scheme != 'https' or not base.host or base.userinfo or base.query or base.fragment
            or not base.path.endswith('/ops/backup/')):
        raise BackupSyncError('A fixed HTTPS backup URL without embedded credentials is required')
    if not config.get('token') or not re.fullmatch(r'[A-Za-z0-9_-]{32,}', config['token']):
        raise BackupSyncError('A strong operations token is required')
    directory = Path(config['destination'])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise BackupSyncError('Destination must not be a symlink')
    received, skipped = 0, 0
    with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + config['token'], 'Accept-Encoding': 'identity'},
                      timeout=60, follow_redirects=False, trust_env=False, transport=transport) as client:
        reserve(directory, MAX_INDEX_BYTES)
        index = io.BytesIO()
        download(client, 'index.json', MAX_INDEX_BYTES, index)
        catalog = json.loads(index.getvalue())
        if catalog.get('version') != 1 or not isinstance(catalog.get('backups'), list) or not catalog['backups']:
            raise BackupSyncError('Unsupported or empty backup catalog')
        names = set()
        for entry in catalog['backups']:
            name, size, digest = entry['filename'], entry['size'], entry['sha256']
            if (not isinstance(name, str) or not NAME.fullmatch(name) or name in names
                    or type(size) is not int or not 0 < size <= MAX_FILE_BYTES
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise BackupSyncError('Invalid or oversized encrypted backup entry')
            names.add(name)
            target = directory / name
            if target.is_symlink():
                raise BackupSyncError('Backup destination must not be a symlink')
            if target.exists():
                if target.stat().st_size != size or fingerprint(target)['sha256'] != digest:
                    raise BackupSyncError('Existing local backup is damaged; refusing overwrite')
                skipped += 1
                continue
            if shutil.disk_usage(directory).free < 5 * 1024 ** 3 + size:
                raise BackupSyncError('Local backup disk has less than 5 GiB free')
            reserve(directory, size)
            with tempfile.TemporaryDirectory(prefix='.partial-', dir=directory) as temporary:
                partial = Path(temporary) / name
                with partial.open('xb') as output:
                    actual = download(client, name, size, output)
                if actual != {'size': size, 'sha256': digest}:
                    raise BackupSyncError('Encrypted backup checksum mismatch')
                partial.chmod(0o600)
                # Publish only a complete verified file; never overwrite a previous copy.
                os.link(partial, target)
            received += 1
        updated = datetime.fromisoformat(catalog['snapshot_at'])
        age = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        if not 0 <= age <= 36:
            raise BackupSyncError('Server encrypted-backup catalog is stale or in the future')
    return {'status': 'passed', 'downloaded': received, 'already_verified': skipped,
            'verified_at': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    directory = Path(config['destination'])
    try:
        result = sync(config)
    except Exception as exc:
        # Never include exception messages, request headers or private configuration in logs.
        result = {'status': 'failed', 'error_type': type(exc).__name__, 'checked_at': datetime.now(timezone.utc).isoformat()}
        if isinstance(exc, BackupSyncError):
            result['reason'] = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            result['http_status'] = exc.response.status_code
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'last-sync.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
        raise SystemExit(1)
    (directory / 'last-sync.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (directory / 'last-success.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
