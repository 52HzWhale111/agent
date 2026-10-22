"""HTTP and backup-freshness checks with persistent incident notifications."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request

from infra.alerts import report as alert

TARGETS = {
    "gateway": "http://gateway:8000/health/ready",
    "knowledge": "http://knowledge:8100/health",
    "sandbox": "http://sandbox:8200/sandbox/run-tests",
    "backend": "http://backend:8000/health",
    "frontend": "http://frontend:3000/",
}


def check(url, authenticated=True):
    try:
        headers = {"Authorization": "Bearer " + os.getenv("GATEWAY_API_KEY", "")} if authenticated else {}
        method = "OPTIONS" if url.rstrip('/').endswith('/sandbox/run-tests') else "GET"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=5) as response:
            return response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def backup_health(index):
    try:
        catalog = json.loads(Path(index).read_text(encoding='utf-8'))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(catalog['snapshot_at'])).total_seconds() / 3600
        return (catalog.get('version') == 1 and bool(catalog.get('backups')) and 0 <= age <= 36
                and shutil.disk_usage(Path(index).parent).free >= 5 * 1024**3)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def poll(targets=None):
    current = {}
    for name, url in (TARGETS if targets is None else targets).items():
        healthy = check(url, authenticated=name == "gateway")
        current[name] = healthy
        alert(name, healthy)
    if os.getenv('BACKUP_INDEX_FILE'):
        current['backup-freshness'] = backup_health(os.environ['BACKUP_INDEX_FILE'])
        alert('backup-freshness', current['backup-freshness'])
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Check once, print status and exit")
    options = parser.parse_args()
    targets = json.loads(os.environ["MONITOR_TARGETS"]) if os.getenv("MONITOR_TARGETS") else TARGETS
    while True:
        current = poll(targets)
        if options.once:
            print(json.dumps({"services": current}))
            raise SystemExit(0 if all(current.values()) else 1)
        time.sleep(max(1, float(os.getenv("MONITOR_INTERVAL", "30"))))


if __name__ == "__main__":
    main()
