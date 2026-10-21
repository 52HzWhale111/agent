from io import BytesIO
import json
from pathlib import Path
import urllib.error

from infra import monitor
import yaml


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
    assert not monitor.alert('sandbox', False)
    assert 'secret' not in capsys.readouterr().out


def test_compose_structure():
    config = yaml.safe_load(Path('infra/docker-compose.yml').read_text(encoding='utf-8'))
    services = config['services']
    assert set(services) == {'gateway', 'knowledge', 'sandbox', 'frontend', 'backend', 'db', 'edge', 'monitor'}
    assert all(item['restart'] == 'unless-stopped' for item in services.values())
    assert services['backend']['depends_on']['db']['condition'] == 'service_healthy'
    assert services['gateway']['ports'] == ['127.0.0.1:8000:8000']
    assert services['sandbox']['volumes'] == ['gateway-data:/data:ro']
    assert 'ports' not in services['db']


def test_monitor_outage_retry_recovery_and_no_duplicates(monkeypatch):
    state = {}
    calls = []
    healthy = True
    delivered = True
    monkeypatch.setattr(monitor, 'check', lambda url, authenticated=True: healthy)
    def notify(service, status):
        calls.append((service, status))
        return delivered
    monkeypatch.setattr(monitor, 'alert', notify)
    targets = {'sandbox': 'http://sandbox/health'}
    monitor.poll(state, targets)
    assert state == {'sandbox': True} and calls == []
    healthy, delivered = False, False
    monitor.poll(state, targets)
    assert state == {'sandbox': True}
    delivered = True
    monitor.poll(state, targets)
    monitor.poll(state, targets)
    assert calls == [('sandbox', False), ('sandbox', False)]
    healthy = True
    monitor.poll(state, targets)
    monitor.poll(state, targets)
    assert calls[-1] == ('sandbox', True) and len(calls) == 3


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
    try:
        state = {}
        targets = {'sandbox': url + '/health'}
        monitor.poll(state, targets)
        healthy = False
        monitor.poll(state, targets)
        monitor.poll(state, targets)
        healthy = True
        monitor.poll(state, targets)
        assert [event['status'] for event in events] == ['unavailable', 'recovered']
        assert all(event['service'] == 'sandbox' for event in events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
