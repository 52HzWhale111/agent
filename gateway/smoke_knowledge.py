"""Three live knowledge flows through business authentication; requires real model configuration."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import time

from docx import Document
import httpx


def main():
    with httpx.Client(trust_env=False, timeout=60) as client:
        health = client.get('http://127.0.0.1:8100/health')
        if health.status_code != 200 or not health.json().get('model_configured'):
            raise SystemExit('Knowledge model is not configured. Fill data/local/knowledge.env and restart infra/start-knowledge.ps1.')
        login = client.post('http://127.0.0.1:8000/api/v1/auth/login', json={'username': 'demo_student', 'password': 'Demo@2026!'})
        login.raise_for_status()
        headers = {'Authorization': 'Bearer ' + login.json()['data']['access_token']}
        url = 'http://127.0.0.1:8000'
        checks = []
        response = client.post(url + '/api/qa', headers=headers, json={'question': '高内聚低耦合到底怎么判断？'})
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer.get('answer') and answer.get('sources') and not answer.get('mock'), answer
        checks.append('retrieval + live model answer + validated source IDs')
        response = client.post(url + '/api/requirements/analyze', headers=headers, json={'description': '做一个校园失物招领系统'})
        assert response.status_code == 200, response.text
        analysis = response.json()
        assert analysis['actors'] and analysis['use_cases'] and analysis['exception_flow'] and not analysis.get('mock')
        checks.append('live structured requirement analysis')
        document = Document()
        document.add_paragraph('校园失物招领需求规格说明书。系统性能要好，界面要美观。学生提交认领申请。')
        content = BytesIO()
        document.save(content)
        uploaded = client.post(url + '/api/uploads', headers=headers, files={'file': ('knowledge-review.docx', content.getvalue())})
        assert uploaded.status_code == 201, uploaded.text
        response = client.post(url + '/api/reviews', headers=headers, json={'document_id': uploaded.json()['document_id'], 'doc_type': 'srs', 'scope': 'excerpt'})
        assert response.status_code == 202, response.text
        task_id = response.json()['task_id']
        for _ in range(35):
            task = client.get(url + '/api/tasks/' + task_id, headers=headers).json()
            if task['status'] in {'succeeded', 'failed'}:
                break
            time.sleep(2)
        assert task['status'] == 'succeeded', task
        review = task['result']
        assert isinstance(review['issues'], list) and not review.get('mock')
        assert sum(review['issue_count'].values()) == len(review['issues'])
        checks.append('authorized DOCX upload + template + live queued review')
        destination = Path('data/local/knowledge-smoke.json')
        destination.write_text(json.dumps({'verified_at': datetime.now(timezone.utc).isoformat(),
            'knowledge_model': 'real configured API', 'checks': checks, 'qa': answer, 'requirements': analysis,
            'review_task_id': task_id, 'review': review,
            'scope': 'Protocol/representative flow validation; 20-case semantic evaluation still requires human grading.'},
            ensure_ascii=False, indent=2), encoding='utf-8')
        print('3 live knowledge flows passed. No token or API key saved. Human domain evaluation remains separate.')


if __name__ == '__main__':
    main()
