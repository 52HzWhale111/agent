"""Persistent incident outbox shared by HTTP monitoring and backup jobs; stdlib only."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
import json
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import time
import urllib.request


@contextmanager
def database(directory=None):
    directory = Path(directory or os.getenv('ALERT_STATE_DIR', 'data/local/alerts/state'))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(directory / 'outbox.sqlite3', timeout=30)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS observed (service TEXT PRIMARY KEY, healthy INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY, service TEXT, healthy INTEGER, time REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS retry (id INTEGER PRIMARY KEY, attempted REAL)')
            yield db
    finally:
        db.close()


def record(service, healthy, directory=None):
    if not re.fullmatch(r'[a-z0-9-]{1,64}', service) or type(healthy) is not bool:
        raise ValueError('Invalid alert event')
    with database(directory) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT healthy FROM observed WHERE service=?', (service,)).fetchone()
        if old is None and healthy:
            db.execute('INSERT INTO observed VALUES (?,1)', (service,))
        elif old is None or bool(old[0]) != healthy:
            db.execute('INSERT INTO pending (service,healthy,time) VALUES (?,?,?)', (service, healthy, time.time()))
            db.execute('INSERT INTO observed VALUES (?,?) ON CONFLICT(service) DO UPDATE SET healthy=excluded.healthy',
                       (service, healthy))


def deliver(event, config_path=None):
    config_path = config_path or os.getenv('ALERT_CONFIG_FILE')
    try:
        if config_path and Path(config_path).is_file():
            config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
            if not config.get('password'):
                raise ValueError('SMTP authorization code is not configured')
            for field in ('username', 'to'):
                address = config[field]
                if '\n' in address or '\r' in address or '@' not in address or parseaddr(address)[1] != address:
                    raise ValueError('Invalid mailbox')
            message = EmailMessage()
            message['Subject'] = '[Zhigou v1] ' + event['status'] + ': ' + event['service']
            message['From'], message['To'], message['Date'] = config['username'], config['to'], formatdate()
            occurred = datetime.fromtimestamp(event['time'], timezone.utc).isoformat()
            message.set_content('知构 v1 运维通知\n服务／任务：' + event['service'] + '\n状态：'
                                + event['status'] + '\n发生时间（UTC）：' + occurred
                                + '\n\n仅包含运行状态；不包含密钥、文档或学生数据。\n')
            client = smtplib.SMTP_SSL(config['host'], int(config.get('port', 465)),
                                     timeout=10, context=ssl.create_default_context())
            try:
                client.login(config['username'], config['password'])
                refused = client.send_message(message)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
            finally:
                client.close()  # A failed QUIT must not retry mail already accepted by SMTP.
            channel = 'smtp'
        elif os.getenv('ALERT_WEBHOOK_URL'):
            request = urllib.request.Request(os.environ['ALERT_WEBHOOK_URL'], data=json.dumps(event).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5):
                pass
            channel = 'webhook'
        else:
            raise ValueError('No alert receiver configured')
        print(json.dumps({'event': 'alert_delivered', 'channel': channel, **event}), flush=True)
        return True
    except Exception as exc:
        # SMTP/server exceptions may contain credentials or message contents; never log their text.
        print(json.dumps({'event': 'alert_delivery_failed', 'service': event['service'],
                          'error_type': type(exc).__name__}), flush=True)
        return False


def flush(directory=None, config_path=None):
    with database(directory) as db:
        # The monitor and systemd hook share this lock; they cannot deliver one event concurrently.
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id,service,healthy,time FROM pending ORDER BY id LIMIT 1').fetchone()
        if row is None:
            return True
        attempt = db.execute('SELECT attempted FROM retry WHERE id=1').fetchone()
        now = time.time()
        if attempt and 0 <= now - attempt[0] < 60:
            return False
        db.execute('INSERT INTO retry VALUES (1,?) ON CONFLICT(id) DO UPDATE SET attempted=excluded.attempted', (now,))
        event = {'service': row[1], 'status': 'recovered' if row[2] else 'unavailable', 'time': row[3]}
        if not deliver(event, config_path):
            return False
        db.execute('DELETE FROM pending WHERE id=?', (row[0],))
        return True


def report(service, healthy, directory=None, config_path=None):
    try:
        record(service, healthy, directory)
        return flush(directory, config_path)
    except Exception as exc:
        print(json.dumps({'event': 'alert_state_failed', 'service': service,
                          'error_type': type(exc).__name__}), flush=True)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-result', action='store_true', help='Record systemd SERVICE_RESULT, including export failures')
    parser.add_argument('--service', default='daily-backup', help='Incident name for the systemd result (also supports isolated drills)')
    parser.add_argument('--test', action='store_true', help='Send one explicitly requested test mail')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if args.test:
        success = deliver({'service': 'notification-test', 'status': 'test', 'time': time.time()}, args.config)
    elif args.backup_result:
        if not os.getenv('SERVICE_RESULT'):
            parser.error('SERVICE_RESULT is required')
        success = report(args.service, os.environ['SERVICE_RESULT'] == 'success', config_path=args.config)
    else:
        success = flush(config_path=args.config)
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
