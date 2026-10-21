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
        'description': '缺少指标', 'suggestion': '补响应时间', 'evidence_lines': [1]}], 'issue_count': {'empty_words': 99}}),
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
        assert len(calls) == (2 if path == '/review_document' else 1)
        if path == '/qa':
            assert 'KP-53' in calls[0]['messages'][0]['content']
            assert 'KP-53' in response.json()['sources'][0]
            assert response.json()['retrieved_count'] <= 5
        if path == '/review_document':
            assert response.json()['issues'][0]['type'] == 'empty_words'
            assert response.json()['issue_count'] == {'missing_section': 0, 'empty_words': 1, 'mismatch': 0}
            assert 'TPL-01' in calls[0]['messages'][0]['content']
            assert 'evidence_lines' not in response.json()['issues'][0]


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
        assert client.post('/review_document', json={'content': ' \n', 'doc_type': 'srs'}).status_code == 422


@pytest.mark.parametrize('content', ['not JSON', '{"answer":"invented","source_ids":["KP-999"]}',
    '{"answer":"出处：教材第X章（KP-XX）","source_ids":["KP-53"]}',
    '{"answer":"解释见SUP-999","source_ids":["KP-53"]}'])
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


def test_sourced_knowledge_gaps_and_original_fields():
    from agents.knowledge.retrieval import format_for_prompt, retrieve
    design = retrieve('概要设计和详细设计有什么区别？')
    quality = retrieve('软件质量保证（sqa）和软件测试有什么区别？')
    assert design[0]['kp_id'] == 'SUP-01' and 'swehb.nasa.gov' in design[0]['source']
    assert quality[0]['kp_id'] == 'SUP-02' and 'computer.org' in quality[0]['source']
    assert '需求阶段' in quality[0]['definition']
    prompt = format_for_prompt(quality)
    assert '忽略了过程审查' in prompt and '教材第16章' in prompt
    assert len(quality) <= 5


def test_qa_reconstructs_sources_instead_of_rejecting_duplicate_footer(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    def model(request):
        value = {'answer': '基线是评审批准并受变更控制的产物。出处：教材第X章（KP-XX）', 'source_ids': ['KP-53']}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/qa', json={'question': '什么是基线'})
        assert response.status_code == 200
        assert response.json()['answer'] == '基线是评审批准并受变更控制的产物。'
        assert '教材第21章' in response.json()['sources'][0]


@pytest.mark.parametrize('issue', [
    {'type': 'mismatch', 'evidence_lines': [999]},
    {'type': 'missing_section', 'evidence_lines': [2], 'missing_item': '非功能需求'},
    {'type': 'missing_section', 'evidence_lines': [2], 'missing_item': '非功能需求（性能/安全）'},
])
def test_review_rejects_invented_evidence_or_existing_section(monkeypatch, issue):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    calls = []
    def model(request):
        calls.append(json.loads(request.content))
        value = {'overall_comment': '错误结果', 'issues': [dict(location='第1节',
            description='错误判断', suggestion='错误建议', **issue)]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/review_document', json={
            'content': '# 需求规格说明书\n## 3. 非功能需求\n响应时间不超过2秒。', 'doc_type': 'srs'})
        assert response.status_code == 502 and len(calls) == 3


@pytest.mark.parametrize('title,scope,expected', [
    ('登录功能用例说明', 'auto', 'excerpt'),
    ('需求规格说明书节选', 'auto', 'excerpt'),
    ('需求规格说明书', 'auto', 'full'),
    ('需求规格说明书', 'excerpt', 'excerpt'),
])
def test_review_scope_and_no_forced_issues(monkeypatch, title, scope, expected):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    def model(request):
        prompt = json.loads(request.content)['messages'][0]['content']
        assert f'本次评审范围：{expected}' in prompt
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            {'overall_comment': '仅评当前文字，未发现明确问题。', 'issues': []})}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/review_document', json={'content': title, 'doc_type': 'srs', 'scope': scope})
        assert response.status_code == 200 and response.json()['issues'] == []


def test_review_verification_removes_unsupported_candidate(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    calls = []
    content = '# 测试报告\n需求覆盖率100%，全部用例通过，无未修复缺陷。\n## 用例统计\n50/50通过。'
    def model(request):
        body = json.loads(request.content)
        calls.append(body)
        assert '已核对的原文事实' in body['messages'][0]['content']
        if len(calls) == 1:
            assert body['thinking'] == {'type': 'disabled'}
            answer = {'overall_comment': '需要编号', 'issues': [
                {'location': '正文', 'type': 'empty_words', 'description': '条目没有编号',
                 'suggestion': '补编号', 'evidence_lines': [1, 2, 3, 4]}]}
        else:
            assert body['thinking'] == {'type': 'enabled'} and body['reasoning_effort'] == 'high'
            data = json.loads(body['messages'][1]['content'])
            assert 'L2: 需求覆盖率100%' in data['document'] and data['candidate']['issues']
            answer = {'overall_comment': '需求覆盖率已满足模板选项。', 'issues': []}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/review_document', json={'content': content, 'doc_type': 'test_report'})
        assert response.status_code == 200 and len(calls) == 2
        assert response.json()['issues'] == []


@pytest.mark.parametrize('coverage,status', [
    ('需求覆盖率100%', 502), ('需求覆盖率5/5=100%', 502),
    ('需求覆盖率目标100%，尚未采集', 200),
])
def test_review_coverage_alternative_requires_observed_value(monkeypatch, coverage, status):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example.test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    def model(request):
        value = {'overall_comment': '缺数据', 'issues': [
            {'location': '正文', 'type': 'missing_section', 'missing_item': '代码覆盖率',
             'description': '没有代码覆盖率数据', 'suggestion': '补充', 'evidence_lines': [2]}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})
    with TestClient(create_app(httpx.MockTransport(model))) as client:
        response = client.post('/review_document', json={'content': '# 测试报告\n' + coverage, 'doc_type': 'test_report'})
        assert response.status_code == status
