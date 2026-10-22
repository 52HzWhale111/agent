"""SMTP privacy, durable incident ordering, and backup hook verification without paid APIs."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import smtplib
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from infra import alerts, monitor, pull_backups


def config(tmp_path):
    path = tmp_path / 'smtp.json'
    path.write_text(json.dumps({'host': 'smtp.example.test', 'port': 465, 'username': 'test@example.test',
                                'password': 'private-authorization-code', 'to': 'owner@example.test'}))
    return path


def test_smtp_uses_verified_tls_and_only_status_fields(tmp_path, monkeypatch, capsys):
    path = config(tmp_path)
    calls = []
    class SMTP:
        def __init__(self, host, port, timeout, context):
            assert (host, port, timeout) == ('smtp.example.test', 465, 10)
            assert context.check_hostname and context.verify_mode == 2
        def login(self, user, password):
            assert user == 'test@example.test' and password == 'private-authorization-code'
        def send_message(self, message):
            calls.append(message)
            return {}
        def close(self):
            pass
    monkeypatch.setattr(alerts.smtplib, 'SMTP_SSL', SMTP)
    event = {'service': 'daily-backup', 'status': 'unavailable', 'time': 1}
    assert alerts.deliver(event, path)
    assert calls[0]['To'] == 'owner@example.test'
    assert 'unavailable' in calls[0]['Subject'] and 'daily-backup' in calls[0].get_content()
    assert 'private-authorization-code' not in calls[0].as_string()
    def refused(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b'private-authorization-code')
    monkeypatch.setattr(alerts.smtplib, 'SMTP_SSL', refused)
    assert not alerts.deliver(event, path)
    output = capsys.readouterr().out
    assert 'SMTPAuthenticationError' in output and 'private-authorization-code' not in output
    assert 'test@example.test' not in output


def test_absent_credentials_and_invalid_address_never_open_smtp(tmp_path, monkeypatch):
    path = config(tmp_path)
    def never(*args, **kwargs):
        pytest.fail('Invalid private configuration must not reach SMTP')
    monkeypatch.setattr(alerts.smtplib, 'SMTP_SSL', never)
    value = json.loads(path.read_text())
    for field, invalid in [('password', ''), ('to', 'owner@example.test\nBcc: injected@example.test')]:
        changed = {**value, field: invalid}
        path.write_text(json.dumps(changed))
        assert not alerts.deliver({'service': 'sandbox', 'status': 'unavailable', 'time': 1}, path)


@pytest.mark.parametrize('result', ['exit-code', 'timeout', 'signal'])
def test_systemd_failure_then_success_is_durable_and_deduplicated(tmp_path, monkeypatch, result):
    monkeypatch.setenv('ALERT_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('SERVICE_RESULT', result)
    monkeypatch.setattr(sys, 'argv', ['alerts', '--backup-result'])
    now = 100
    monkeypatch.setattr(alerts.time, 'time', lambda: now)
    delivered = False
    events = []
    def send(event, config_path=None):
        if delivered:
            events.append(event)
        return delivered
    monkeypatch.setattr(alerts, 'deliver', send)
    with pytest.raises(SystemExit) as exit_code:
        alerts.main()
    assert exit_code.value.code == 1
    alerts.report('daily-backup', False)
    monkeypatch.setenv('SERVICE_RESULT', 'success')
    with pytest.raises(SystemExit):
        alerts.main()
    with sqlite3.connect(tmp_path / 'outbox.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 2
    delivered = True
    for _ in range(2):
        now += 61
        assert alerts.flush()
    assert [(e['service'], e['status']) for e in events] == [('daily-backup', 'unavailable'), ('daily-backup', 'recovered')]
    assert alerts.report('daily-backup', True)
    assert len(events) == 2


def test_freshness_catches_missing_stale_and_low_disk(tmp_path, monkeypatch):
    index = tmp_path / 'index.json'
    assert not monitor.backup_health(index)
    monkeypatch.setattr(monitor.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10*1024**3))
    value = {'version': 1, 'snapshot_at': datetime.now(timezone.utc).isoformat(), 'backups': [{}]}
    index.write_text(json.dumps(value))
    assert monitor.backup_health(index)
    monkeypatch.setattr(monitor.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    assert not monitor.backup_health(index)
    monkeypatch.setattr(monitor.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10*1024**3))
    value['snapshot_at'] = (datetime.now(timezone.utc)-timedelta(hours=37)).isoformat()
    index.write_text(json.dumps(value))
    assert not monitor.backup_health(index)


def test_offsite_failure_preserves_backup_exit_when_alert_transport_fails(tmp_path, monkeypatch):
    path = tmp_path / 'pull.json'
    path.write_text(json.dumps({'destination': str(tmp_path / 'copies'), 'alert_config': str(config(tmp_path))}))
    monkeypatch.setattr(sys, 'argv', ['pull_backups', '--config', str(path)])
    events = []
    monkeypatch.setattr(pull_backups, 'report', lambda *args: events.append(args) or False)
    def failed(value):
        raise pull_backups.BackupSyncError('Server encrypted-backup catalog is stale or in the future')
    monkeypatch.setattr(pull_backups, 'sync', failed)
    with pytest.raises(SystemExit) as failure:
        pull_backups.main()
    assert failure.value.code == 1 and events[0][:2] == ('offsite-backup', False)
    monkeypatch.setattr(pull_backups, 'sync', lambda value: {'status': 'passed'})
    pull_backups.main()
    assert events[-1][:2] == ('offsite-backup', True)
    assert json.loads((tmp_path / 'copies' / 'last-success.json').read_text())['status'] == 'passed'
