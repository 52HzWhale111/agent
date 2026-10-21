"""Protocol checks use a model double; these are not real model evaluations."""
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from agents.knowledge.api import create_app


@pytest.mark.parametrize('path,payload,result', [
    ('/qa', {'question': '什么是基线'}, {'answer': '通过正式评审并受变更控制的产物。', 'source_ids': ['KP-53']}),
    ('/analyze_requirement', {'description': '校园失物招领'}, {
        'actors': [{'name': '学生', 'description': '发布物品'}],
        'use_cases': [{'name': '认领', 'description': '验证物品归属'}], 'main_flow': ['核实后归还'],
        'exception_flow': [{'name': '重复认领', 'description': '多人认领', '处理方式': '人工核实'}], 'notes': '示例'}),
    ('/review_document', {'content': '系统性能要好。', 'doc_type': 'srs'}, {
        'overall_comment': '需量化指标', 'issues': [{'location': '第1段', 'type': '空话',
        'description': '缺少指标', 'suggestion': '补响应时间'}], 'issue_count': {'empty_words': 99}}),
])
def test_knowledge_protocol_prompts_and_validation(monkeypatch, path, payload, result):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret-never-log')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test/v1')
    monkeypatch.setenv('LLM_MODEL', 'qwen-plus')
    calls = []
    def model(request):
        calls.append(json.loads(request.content))
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-secret-never-log'
        assert calls[-1]['enable_thinking'] is False
        assert calls[-1]['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        assert client.get('/health').status_code == 200
        response = client.post(path, json=payload, headers={'X-Request-ID': 'knowledge-contract-1'})
        assert response.status_code == 200, response.text
        assert response.headers['X-Request-ID'] == 'knowledge-contract-1'
        assert len(calls) == 1
        if path == '/qa':
            assert 'KP-53' in calls[0]['messages'][0]['content']
            assert 'KP-53' in response.json()['sources'][0]
            assert response.json()['retrieved_count'] <= 5
        if path == '/review_document':
            assert response.json()['issues'][0]['type'] == 'empty_words'
            assert response.json()['issue_count'] == {'missing_section': 0, 'empty_words': 1, 'mismatch': 0}
            assert 'TPL-01' in calls[0]['messages'][0]['content']


def test_knowledge_missing_config_and_no_retrieval(monkeypatch):
    for name in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app()) as client:
        assert client.get('/health').status_code == 503
        response = client.post('/qa', json={'question': '什么是基线'})
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'MODEL_NOT_CONFIGURED'
        response = client.post('/qa', json={'question': '今天天气如何'})
        assert response.json()['retrieved_count'] == 0 and response.json()['sources'] == []
        assert client.post('/review_document', json={'content': 'x', 'doc_type': 'unknown'}).status_code == 422


@pytest.mark.parametrize('content', ['not JSON', '{"answer":"invented","source_ids":["KP-999"]}'])
def test_knowledge_invalid_model_output_retries_once(monkeypatch, content):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    calls = []
    def model(request):
        calls.append(json.loads(request.content))
        assert calls[-1]['thinking'] == {'type': 'disabled'}
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/qa', json={'question': '什么是基线'})
        assert response.status_code == 502
        assert response.json()['detail']['code'] == 'INVALID_MODEL_RESPONSE'
        assert len(calls) == 2
