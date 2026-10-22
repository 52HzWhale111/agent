"""Bounded, authenticated backup pulls without external network or model calls."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from infra import pull_backups


@pytest.fixture(autouse=True)
def enough_test_disk(monkeypatch):
    # Unit tests should not require 5 GiB of unused space on the OS temporary drive.
    monkeypatch.setattr(pull_backups.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20*1024**3))


def setup(tmp_path, data=b'encrypted backup example'):
    name = 'snapshot-20261022T053336Z-171088.cms'
    entry = {'filename': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    catalog = {'version': 1, 'snapshot_at': datetime.now(timezone.utc).isoformat(), 'backups': [entry]}
    config = {'base_url': 'https://backup.example.test/ops/backup/', 'token': 'x'*48, 'destination': str(tmp_path/'copies')}
    return name, catalog, config


def test_pull_catches_up_and_repeated_runs_verify_without_downloading(tmp_path):
    data = b'actual encrypted bytes'
    name, catalog, config = setup(tmp_path, data)
    paths = []
    def server(request):
        assert request.headers['Authorization'] == 'Bearer ' + config['token']
        paths.append(request.url.path)
        return httpx.Response(200, json=catalog) if request.url.path.endswith('index.json') else httpx.Response(200, content=data)
    transport = httpx.MockTransport(server)
    first = pull_backups.sync(config, transport)
    second = pull_backups.sync(config, transport)
    assert first['downloaded'] == 1 and second['downloaded'] == 0 and second['already_verified'] == 1
    assert paths.count('/ops/backup/' + name) == 1
    assert (tmp_path/'copies'/name).read_bytes() == data
    with sqlite3.connect(tmp_path/'copies'/'transfer-budget.sqlite3') as db:
        assert db.execute('SELECT reserved FROM bandwidth').fetchone()[0] == 2*pull_backups.MAX_INDEX_BYTES + len(data)


@pytest.mark.parametrize('failure', ['corrupt', 'oversized', 'redirect', 'unauthorized', 'unsafe_path', 'disk_full'])
def test_failed_pull_leaves_no_published_file_or_partial(tmp_path, monkeypatch, failure):
    data = b'actual encrypted bytes'
    name, catalog, config = setup(tmp_path, data)
    calls = []
    if failure == 'unsafe_path':
        catalog['backups'][0]['filename'] = '../outside.cms'
    if failure == 'disk_full':
        monkeypatch.setattr(pull_backups.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    def server(request):
        calls.append(str(request.url))
        if failure == 'unauthorized':
            return httpx.Response(404)
        if failure == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://untrusted.example.test/secret'})
        if request.url.path.endswith('index.json'):
            return httpx.Response(200, json=catalog)
        return httpx.Response(200, content=b'wrong' if failure == 'corrupt' else data+b'overflow')
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        pull_backups.sync(config, httpx.MockTransport(server))
    assert not list((tmp_path/'copies').glob('*.cms'))
    assert not list((tmp_path/'copies').glob('.partial-*'))
    assert all(url.startswith('https://backup.example.test/') for url in calls)


def test_transfer_limit_persists_and_blocks_before_network(tmp_path):
    _, _, config = setup(tmp_path)
    directory = tmp_path/'copies'
    directory.mkdir()
    pull_backups.reserve(directory, pull_backups.MONTHLY_BYTES)
    def never(request):
        pytest.fail('An exhausted transfer budget must not make HTTP requests')
    for _ in range(2):
        with pytest.raises(ValueError, match='Monthly'):
            pull_backups.sync(config, httpx.MockTransport(never))


def test_cached_corruption_is_not_overwritten_and_stale_snapshot_is_reported(tmp_path):
    name, catalog, config = setup(tmp_path)
    directory = tmp_path/'copies'
    directory.mkdir()
    (directory/name).write_bytes(b'keep damaged copy for investigation')
    def server(request):
        assert request.url.path.endswith('index.json')
        return httpx.Response(200, json=catalog)
    with pytest.raises(ValueError, match='damaged'):
        pull_backups.sync(config, httpx.MockTransport(server))
    assert (directory/name).read_bytes() == b'keep damaged copy for investigation'
    (directory/name).unlink()
    catalog['snapshot_at'] = (datetime.now(timezone.utc)-timedelta(hours=48)).isoformat()
    def old_server(request):
        return httpx.Response(200, json=catalog) if request.url.path.endswith('index.json') else httpx.Response(200, content=b'encrypted backup example')
    with pytest.raises(ValueError, match='stale'):
        pull_backups.sync(config, httpx.MockTransport(old_server))
    assert (directory/name).exists()  # Old but valid data is still copied before reporting freshness failure.


@pytest.mark.parametrize('url', ['http://backup.example.test/ops/backup/', 'https://secret@backup.example.test/ops/backup/', 'https://backup.example.test/ops/backup/?token=secret'])
def test_rejects_cleartext_and_embedded_credentials(tmp_path, url):
    _, _, config = setup(tmp_path)
    config['base_url'] = url
    with pytest.raises(ValueError, match='HTTPS'):
        pull_backups.sync(config)


def test_task_status_preserves_last_success_and_never_logs_http_credentials(tmp_path, monkeypatch, capsys):
    import sys
    _, _, config = setup(tmp_path)
    directory = tmp_path/'copies'
    directory.mkdir()
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, 'argv', ['pull_backups', '--config', str(path)])
    success = {'status': 'passed', 'downloaded': 1, 'already_verified': 0}
    monkeypatch.setattr(pull_backups, 'sync', lambda value: success)
    pull_backups.main()
    request = httpx.Request('GET', config['base_url'], headers={'Authorization': config['token']})
    def failed(value):
        raise httpx.HTTPStatusError(config['token'], request=request, response=httpx.Response(404, request=request))
    monkeypatch.setattr(pull_backups, 'sync', failed)
    with pytest.raises(SystemExit):
        pull_backups.main()
    assert json.loads((directory/'last-success.json').read_text()) == success
    latest = json.loads((directory/'last-sync.json').read_text())
    assert latest['status'] == 'failed' and latest['http_status'] == 404
    assert config['token'] not in capsys.readouterr().out
    assert config['token'] not in (directory/'last-sync.json').read_text()
