from io import BytesIO
import json
from pathlib import Path
import urllib.error

from infra import alerts, monitor
import pytest
import yaml


@pytest.fixture(autouse=True)
def isolated_alerts(tmp_path, monkeypatch):
    monkeypatch.setenv('ALERT_STATE_DIR', str(tmp_path / 'alerts'))
    monkeypatch.delenv('ALERT_CONFIG_FILE', raising=False)
    monkeypatch.delenv('BACKUP_INDEX_FILE', raising=False)


def test_monitor_checks_and_webhook(monkeypatch, capsys):
    calls = []
    def ok(request, timeout):
        calls.append(request)
        response = BytesIO(b'{}')
        response.status = 200
        return response
    monkeypatch.setenv('GATEWAY_API_KEY', 'secret')
    monkeypatch.setenv('ALERT_WEBHOOK_URL', 'https://notifications.example.test')
    monkeypatch.setattr(monitor.urllib.request, 'urlopen', ok)
    assert monitor.check('http://gateway/health/ready')
    assert calls[0].get_header('Authorization') == 'Bearer secret'
    assert monitor.alert('sandbox', False)
    assert json.loads(calls[-1].data)['status'] == 'unavailable'
    def fail(*args, **kwargs):
        raise urllib.error.URLError('offline')
    monkeypatch.setattr(monitor.urllib.request, 'urlopen', fail)
    assert not monitor.check('http://gateway/health/ready')
    assert not alerts.deliver({'service': 'sandbox', 'status': 'unavailable', 'time': 1})
    assert 'secret' not in capsys.readouterr().out


def test_compose_structure():
    config = yaml.safe_load(Path('infra/docker-compose.yml').read_text(encoding='utf-8'))
    services = config['services']
    assert set(services) == {'gateway', 'knowledge', 'sandbox', 'sandbox-execution', 'frontend', 'backend', 'db', 'edge', 'monitor'}
    assert services['sandbox-execution']['profiles'] == ['build']
    assert all(item['restart'] == 'unless-stopped' for name, item in services.items() if name != 'sandbox-execution')
    assert services['backend']['depends_on']['db']['condition'] == 'service_healthy'
    assert services['gateway']['ports'] == ['127.0.0.1:8000:8000']
    assert 'ports' not in services['sandbox']
    assert '/var/run/docker.sock:/var/run/docker.sock:ro' in services['sandbox']['volumes']
    assert services['sandbox']['read_only'] and services['sandbox']['cap_drop'] == ['ALL']
    assert services['sandbox']['networks'] == ['sandbox-internal']
    assert config['networks']['sandbox-internal']['internal']
    assert 'ports' not in services['db']


def test_monitor_outage_retry_recovery_and_no_duplicates(monkeypatch):
    calls = []
    healthy = True
    delivered = True
    now = 100
    monkeypatch.setattr(alerts.time, 'time', lambda: now)
    monkeypatch.setattr(monitor, 'check', lambda url, authenticated=True: healthy)
    def notify(event, config_path=None):
        calls.append((event['service'], event['status']))
        return delivered
    monkeypatch.setattr(alerts, 'deliver', notify)
    targets = {'sandbox': 'http://sandbox/health'}
    monitor.poll(targets)
    assert calls == []
    healthy, delivered = False, False
    monitor.poll(targets)
    delivered = True
    monitor.poll(targets)
    assert len(calls) == 1  # Delivery retries are limited to once a minute.
    healthy = True
    now += 61
    monitor.poll(targets)  # Even a recovered outage still delivers its original failure first.
    now += 61
    monitor.poll(targets)
    monitor.poll(targets)
    assert calls == [('sandbox', 'unavailable'), ('sandbox', 'unavailable'), ('sandbox', 'recovered')]
    # State survives process restarts; the next poll has no in-memory history.
    now += 61
    monitor.poll(targets)
    assert len(calls) == 3


def test_monitor_real_http_webhook(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    events = []
    healthy = True
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get('Authorization') is None
            self.send_response(200 if healthy else 503)
            self.end_headers()
        def do_POST(self):
            events.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('ALERT_WEBHOOK_URL', url + '/notifications')
    now = 100
    monkeypatch.setattr(alerts.time, 'time', lambda: now)
    try:
        targets = {'sandbox': url + '/health'}
        monitor.poll(targets)
        healthy = False
        monitor.poll(targets)
        monitor.poll(targets)
        healthy = True
        now += 61
        monitor.poll(targets)
        assert [event['status'] for event in events] == ['unavailable', 'recovered']
        assert all(event['service'] == 'sandbox' for event in events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
