from io import BytesIO
import json
import sqlite3
import time

from docx import Document
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.api import create_app as create_backend
from backend.app.config import Settings
from gateway.main import create_app


def completed(client, task_id, headers=None):
    for _ in range(100):
        body = client.get('/api/tasks/' + task_id, headers=headers).json()
        if body['status'] in {'succeeded', 'failed'}:
            return body
        time.sleep(.03)
    raise AssertionError('Task did not finish')


@pytest.mark.parametrize('status', ['SUCCESS', 'FAILED', 'COMPILE_ERROR', 'TIMEOUT', 'NO_TESTS', 'ERROR'])
def test_engine_text_protocol_and_statuses(tmp_path, monkeypatch, status):
    monkeypatch.setenv('SANDBOX_PROTOCOL', 'text')
    calls = []
    def engine(request):
        if request.method == 'OPTIONS':
            assert request.url.path == '/sandbox/run-tests'
            return httpx.Response(204)
        if request.url.path == '/health':
            return httpx.Response(200, json={})
        calls.append(request)
        assert request.url.path == '/sandbox/run-tests'
        assert request.headers['Content-Type'].startswith('text/plain')
        assert request.content.decode() == 'public class Main {}\n---TEST---\npublic class MainTest {}'
        assert request.headers.get('X-Request-ID')
        return httpx.Response(200, json={'status': status, 'tests': {'success': 1, 'failed': 0},
                                        'coverage': {'line': 100.0}, 'output': 'console'})
    with TestClient(create_app(tmp_path, httpx.MockTransport(engine))) as client:
        response = client.post('/api/code-tests', json={'code': 'public class Main {}', 'tests': 'public class MainTest {}'})
        assert response.status_code == 202
        result = completed(client, response.json()['task_id'])
        if status == 'ERROR':
            assert result['status'] == 'failed' and result['error']['code'] == 'SANDBOX_ERROR'
        else:
            assert result['status'] == 'succeeded'
            assert result['result']['status'] == status
            assert result['result']['coverage'] == {'line': 100.0}
        assert len(calls) == 1
        assert client.post('/api/code-tests', json={'code': 'class Main {}'}).status_code == 422
        assert client.post('/api/code-tests', json={'file_id': 'archive'}).status_code == 422
        assert client.post('/api/consistency', json={'code': 'class Main {}', 'diagram': {}}).status_code == 503
        health = client.get('/health/ready').json()
        assert health['services']['sandbox'] == 'http_route_only'


def test_real_backend_auth_upload_review_and_user_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('BACKEND_URL', 'http://backend.test')
    monkeypatch.setenv('SANDBOX_PROTOCOL', 'text')
    settings = Settings(database_url=f'sqlite:///{(tmp_path / "business.db").as_posix()}',
                        secret_key='private-test-key-at-least-thirty-two-characters', upload_dir=tmp_path / 'business-files')
    business = create_backend(settings)
    agents = []
    class TeamTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.host == 'backend.test':
                return await httpx.ASGITransport(app=business).handle_async_request(request)
            payload = json.loads(request.content)
            agents.append((request.url.path, payload))
            return httpx.Response(200, json={'answer': 'test', 'issues': [], 'received': payload.get('content')})
    with TestClient(business), TestClient(create_app(tmp_path / 'gateway', TeamTransport())) as client:
        def account(username):
            registered = client.post('/api/v1/auth/register', json={'username': username, 'password': 'Student@2026', 'display_name': username})
            assert registered.status_code == 201, registered.text
            user_id = registered.json()['data']['id']
            login = client.post('/api/v1/auth/login', json={'username': username, 'password': 'Student@2026'})
            assert login.status_code == 200
            return user_id, {'Authorization': 'Bearer ' + login.json()['data']['access_token']}
        user_id, first = account('first_student')
        _, second = account('second_student')
        assert client.post('/api/qa', json={'question': 'q'}).status_code == 401
        assert client.post('/api/qa', headers={'Authorization': 'Bearer invalid'}, json={'question': 'q'}).status_code == 401
        assert client.post('/api/qa', headers=first, json={'question': 'q', 'user_id': 'spoofed'}).status_code == 200
        assert agents[-1][1]['user_id'] == user_id
        document = Document()
        document.add_paragraph('SRS body from business storage')
        content = BytesIO()
        document.save(content)
        upload = client.post('/api/uploads', headers=first, files={'file': ('srs.docx', content.getvalue())})
        assert upload.status_code == 201, upload.text
        document_id = upload.json()['document_id']
        assert document_id == upload.json()['file_id']
        assert client.get('/api/v1/documents', headers=first).json()['data']['total'] == 1
        original = client.get(f'/api/v1/documents/{document_id}/content', headers=first)
        assert original.content == content.getvalue()
        assert original.headers['X-Backend-Request-ID']
        assert client.get(f'/api/v1/documents/{document_id}/content', headers=second).status_code == 404
        assert client.post('/api/reviews', headers=second, json={'document_id': document_id, 'doc_type': 'srs'}).status_code == 404
        review = client.post('/api/reviews', headers=first, json={'document_id': document_id, 'doc_type': 'srs'})
        assert review.status_code == 202, review.text
        task_id = review.json()['task_id']
        result = completed(client, task_id, first)
        assert result['status'] == 'succeeded', result
        assert 'SRS body from business storage' in result['result']['received']
        assert client.get('/api/tasks/' + task_id, headers=second).status_code == 404
        assert client.get('/api/tasks/' + task_id).status_code == 401
        with sqlite3.connect(tmp_path / 'gateway' / 'gateway.sqlite3') as conn:
            values = conn.execute('SELECT owner_id,payload FROM tasks WHERE id=?', (task_id,)).fetchone()
            assert values[0] == user_id and 'Bearer' not in values[1]
        assert client.get('/business/api/v1/auth/me', headers=first).json()['data']['id'] == user_id
