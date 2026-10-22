"""Publish encrypted backups and an atomic catalog; plaintext never enters the download directory."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

from infra.snapshot import encrypt, health
from infra.gateway_backup import fingerprint

NAME = re.compile(r"snapshot-\d{8}T\d{6}Z-\d+\.cms")


def publish(backups, destination, certificate):
    backups, destination = Path(backups), Path(destination)
    candidates = sorted(backups.glob('snapshot-*/snapshot.json'))
    if not candidates:
        raise ValueError('No complete snapshot is available')
    source = candidates[-1].parent
    health(source)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.is_symlink() or destination.resolve().is_relative_to(source.resolve()):
        raise ValueError('Export directory must be separate from the snapshot')
    name = source.name + '.cms'
    if not NAME.fullmatch(name):
        raise ValueError('Unexpected snapshot name')
    encrypted = destination / name
    if not encrypted.exists():
        required = sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
        if shutil.disk_usage(destination).free < 5 * 1024 ** 3 + 2 * required:
            raise ValueError('Insufficient space for encrypted export')
        encrypt(source, encrypted, certificate)
    entries = []
    for path in sorted(destination.glob('*.cms')):
        if path.is_symlink() or not NAME.fullmatch(path.name):
            raise ValueError('Unsafe encrypted export')
        entries.append({'filename': path.name, **fingerprint(path)})
    snapshot_at = json.loads((source / 'snapshot.json').read_text(encoding='utf-8'))['created_at']
    catalog = {'version': 1, 'updated_at': datetime.now(timezone.utc).isoformat(), 'snapshot_at': snapshot_at, 'backups': entries}
    temporary = destination / '.index.json.partial'
    temporary.write_text(json.dumps(catalog, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(destination / 'index.json')
    return {'published': name, 'available_backups': len(entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backups', type=Path, default=Path('backups'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--certificate', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.backups, args.output, args.certificate)))


if __name__ == '__main__':
    main()
