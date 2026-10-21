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
