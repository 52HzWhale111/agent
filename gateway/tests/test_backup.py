from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zipfile

from fastapi.testclient import TestClient
import pytest

from gateway.main import create_app
from infra.gateway_backup import backup, restore, verify


def make_source(path):
    data = BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('Main.java', 'class Main {}')
    with TestClient(create_app(path, start_worker=False)) as client:
        uploaded = client.post('/api/uploads', files={'file': ('project.zip', data.getvalue())}).json()
        task = client.post('/api/code-tests', json={'file_id': uploaded['file_id']}).json()
    return uploaded['file_id'], task['task_id']


def test_online_backup_restore_and_rebase(tmp_path):
    source, snapshot, restored = (tmp_path / name for name in ['source', 'snapshot', 'restored'])
    file_id, task_id = make_source(source)
    manifest = backup(source, snapshot)
    assert verify(snapshot) == manifest
    assert restore(snapshot, restored) == {'restored_uploads': 1, 'rebased_tasks': 1}
    with sqlite3.connect(restored / 'gateway.sqlite3') as conn:
        status, value = conn.execute('SELECT status, payload FROM tasks WHERE id=?', (task_id,)).fetchone()
    assert status == 'queued'
    assert Path(json.loads(value)['archive_path']) == restored / 'uploads' / (file_id + '.zip')
    assert (source / 'uploads' / (file_id + '.zip')).read_bytes() == (restored / 'uploads' / (file_id + '.zip')).read_bytes()
    with pytest.raises(FileExistsError):
        restore(snapshot, source)
    with pytest.raises(FileExistsError):
        backup(source, snapshot)


def test_corruption_missing_files_and_unsafe_manifest_fail(tmp_path):
    source = tmp_path / 'source'
    file_id, _ = make_source(source)
    snapshot = tmp_path / 'snapshot'
    backup(source, snapshot)
    filename = snapshot / 'uploads' / (file_id + '.zip')
    original = filename.read_bytes()
    filename.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        restore(snapshot, tmp_path / 'restore-corrupted')
    assert not (tmp_path / 'restore-corrupted').exists()
    filename.write_bytes(original)
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    manifest['files']['../outside'] = {'size': 0, 'sha256': ''}
    (snapshot / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Unsafe'):
        verify(snapshot)
    (source / 'uploads' / (file_id + '.zip')).unlink()
    with pytest.raises(FileNotFoundError):
        backup(source, tmp_path / 'incomplete')
    assert not (tmp_path / 'incomplete' / 'manifest.json').exists()
