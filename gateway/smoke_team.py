"""Live checks for a real backend and local Agent doubles; no tokens are saved."""
from io import BytesIO
import json
from pathlib import Path
import time

from docx import Document
import httpx


def main():
    checks = []
    def passed(name):
        checks.append(name)
        print('PASS ' + name, flush=True)
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=20) as client:
        def login(username):
            response = client.post('/api/v1/auth/login', json={'username': username, 'password': 'Demo@2026!'})
            assert response.status_code == 200, response.text
            return {'Authorization': 'Bearer ' + response.json()['data']['access_token']}
        first, second = login('demo_student'), login('demo_new')
        passed('real backend login through gateway')
        assert client.get('/api/v1/knowledge-points').json()['data']['total'] == 60
        assert client.get('/api/v1/cases').json()['data']['total'] == 20
        passed('60 knowledge points and 20 cases from actual database')
        assert client.post('/api/qa', json={'question': 'q'}).status_code == 401
        passed('Agent API requires a real login token')
        document = Document()
        document.add_paragraph('Local SRS review integration sample')
        content = BytesIO()
        document.save(content)
        response = client.post('/api/uploads', headers=first, files={'file': ('integration.docx', content.getvalue())})
        assert response.status_code == 201, response.text
        document_id = response.json()['document_id']
        original = client.get(f'/api/v1/documents/{document_id}/content', headers=first)
        assert original.content == content.getvalue()
        assert client.get(f'/api/v1/documents/{document_id}/content', headers=second).status_code == 404
        passed('real document upload, download and ownership checks')
        def finish(task_id):
            for _ in range(12):
                result = client.get('/api/tasks/' + task_id, headers=first).json()
                if result['status'] in {'succeeded', 'failed'}:
                    return result
                time.sleep(2)
            raise AssertionError('Task did not finish')
        task = client.post('/api/reviews', headers=first, json={'document_id': document_id, 'doc_type': 'srs'})
        assert task.status_code == 202, task.text
        task_id = task.json()['task_id']
        result = finish(task_id)
        assert result['status'] == 'succeeded' and result['result']['received_characters'] > 0
        assert result['result']['mock'] is True
        assert client.get('/api/tasks/' + task_id, headers=second).status_code == 404
        passed('review gets authorized document text; task belongs to logged-in user')
        test = client.post('/api/code-tests', headers=first, json={'code': 'public class Main {}', 'tests': 'public class MainTest {}'})
        assert test.status_code == 202, test.text
        result = finish(test.json()['task_id'])
        assert result['status'] == 'succeeded' and result['result']['mock'] is True
        assert result['result']['status'] == 'MOCK' and result['result']['coverage']['line'] is None
        passed('engineering text protocol (double only; no Java executed)')
        assert client.post('/api/code-tests', headers=first, json={'code': 'class Main {}'}).status_code == 422
        assert client.post('/api/consistency', headers=first, json={'code': 'class Main {}', 'diagram': {}}).json()['error']['code'] == 'FEATURE_UNAVAILABLE'
        passed('missing tests and unimplemented consistency are reported honestly')
        diagram = {'title': 'Gateway integration sample', 'diagram_type': 'class', 'data': {
            'nodes': [{'id': 'main', 'type': 'class', 'label': 'Main', 'position': {'x': 0, 'y': 0}}], 'edges': []}}
        created = client.post('/api/v1/diagrams', headers=first, json=diagram)
        assert created.status_code == 201, created.text
        diagram_id = created.json()['data']['diagram_id']
        assert client.put('/api/v1/diagrams/' + diagram_id, headers=first, json={**diagram, 'base_version': 1}).status_code == 200
        assert client.put('/api/v1/diagrams/' + diagram_id, headers=first, json={**diagram, 'base_version': 1}).status_code == 409
        passed('real UML storage, version save and conflict response')
        weak = login('demo_weak')
        diagnosis = client.get('/api/v1/learning/diagnosis', headers=weak).json()['data']
        assert len(diagnosis['weak_points']) == 3 and len(diagnosis['recommended_questions']) == 5
        passed('real learning diagnosis and five recommended questions')
        health = client.get('/health/ready')
        assert health.status_code == 200, health.text
        passed('gateway, backend DB and Agent routes reachable')
    report = {'checks': checks, 'passed': len(checks), 'business_backend': 'real', 'knowledge_agent': 'mock',
              'engineering_agent': 'mock', 'document_id': document_id, 'review_task_id': task_id,
              'diagram_id': diagram_id}
    Path('data/local/team-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'{len(checks)} live integration checks passed; no real Agent execution was claimed.')


if __name__ == '__main__':
    main()
