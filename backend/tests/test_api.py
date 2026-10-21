"""HTTP behavior checks for authorization, persistence, grading and input boundaries."""
import copy
import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.models import Exercise, User

P = "/api/v1"


def data(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == "OK"
    assert body["request_id"] == response.headers["X-Request-ID"]
    return body["data"]


def error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == code
    assert body["request_id"] == response.headers["X-Request-ID"]
    return body


def question(client, question_id="Q-01"):
    with client.app.state.session_factory() as session:
        row = session.get(Exercise, question_id)
        return {"id": row.id, "correct": row.answer["correct_option"], "options": [o["id"] for o in row.content["options"]], "points": row.knowledge_point_ids}


def answer_body(q, event="event-0000001", correct=True):
    option = q["correct"] if correct else next(o for o in q["options"] if o != q["correct"])
    return {"exercise_id": q["id"], "answer": option, "client_event_id": event}


def test_health_and_openapi_contract(client):
    assert data(client.get("/health")) == {"status": "ok", "database": "ok"}
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "知构业务接口"
    assert P + "/learning/answers" in spec["paths"]
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def test_register_normalizes_username_and_never_returns_password(client):
    payload = {"username": "Student_A", "password": "Password@2026", "display_name": "  同学甲  "}
    user = data(client.post(P + "/auth/register", json=payload), 201)
    assert user["username"] == "student_a" and user["display_name"] == "同学甲"
    assert set(user) == {"id", "username", "display_name", "created_at"}
    error(client.post(P + "/auth/register", json={**payload, "username": "STUDENT_A"}), 409, "USERNAME_EXISTS")
    logged = data(client.post(P + "/auth/login", json={"username": "STUDENT_A", "password": payload["password"]}))
    assert logged["user"] == user
    with client.app.state.session_factory() as session:
        stored = session.get(User, user["id"])
        assert stored.password_hash.startswith("$argon2") and stored.password_hash != payload["password"]


def test_login_errors_and_private_route_require_token(client):
    error(client.get(P + "/auth/me"), 401, "AUTH_REQUIRED")
    error(client.get(P + "/auth/me", headers={"Authorization": "Bearer invalid"}), 401, "TOKEN_INVALID")
    for username in ("demo_new", "missing_user"):
        error(client.post(P + "/auth/login", json={"username": username, "password": "wrong"}), 401, "INVALID_CREDENTIALS")


def test_expired_token_is_rejected(client, login):
    me = data(client.get(P + "/auth/me", headers=login()))
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": me["id"], "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1), "iss": "zhigou-backend", "aud": "zhigou"}, client.app.state.settings.secret_key, algorithm="HS256")
    error(client.get(P + "/auth/me", headers={"Authorization": f"Bearer {token}"}), 401, "TOKEN_INVALID")


def test_validation_error_does_not_echo_password(client):
    secret = "private-value-that-must-not-be-echoed"
    response = client.post(P + "/auth/register", json={"username": "x", "password": secret, "display_name": "甲"})
    error(response, 422, "VALIDATION_ERROR")
    assert secret not in response.text
    error(client.get("/api/v1/not-a-route"), 404, "NOT_FOUND")


def test_knowledge_import_counts_filter_and_pagination(client):
    all_points = data(client.get(P + "/knowledge-points", params={"page_size": 100}))
    assert all_points["total"] == 60
    assert [p["id"] for p in all_points["items"]] == [f"KP-{n:02}" for n in range(1, 61)]
    first_chapter = data(client.get(P + "/knowledge-points", params={"chapter": 1, "page_size": 3}))
    second_page = data(client.get(P + "/knowledge-points", params={"chapter": 1, "page_size": 3, "page": 2}))
    assert first_chapter["total"] == second_page["total"] == 8
    assert len(first_chapter["items"]) == len(second_page["items"]) == 3
    assert {p["id"] for p in first_chapter["items"]}.isdisjoint(p["id"] for p in second_page["items"])
    assert all(p["chapter"] == 1 for p in first_chapter["items"])
    target = all_points["items"][0]
    matches = data(client.get(P + "/knowledge-points", params={"keyword": target["title"], "page_size": 100}))
    assert target["id"] in {p["id"] for p in matches["items"]}
    assert data(client.get(P + "/knowledge-points", params={"keyword": "%"}))["total"] == 0
    assert data(client.get(P + "/knowledge-points/KP-01"))["source"]
    error(client.get(P + "/knowledge-points/KP-99"), 404, "KNOWLEDGE_NOT_FOUND")
    error(client.get(P + "/knowledge-points", params={"page_size": 101}), 422, "VALIDATION_ERROR")


def test_cases_and_public_questions_keep_scoring_answers_private(client):
    cases = data(client.get(P + "/cases", params={"page_size": 100}))
    assert cases["total"] == 20
    assert all(item["kind"] == "case" for item in cases["items"])
    case = data(client.get(P + "/cases/RC-01"))
    assert case["answer_points"] and case["content"]
    questions = data(client.get(P + "/questions", params={"page_size": 100}))
    assert questions["total"] >= 15
    assert "correct_option" not in str(questions)
    assert all("answer" not in item for item in questions["items"])
    target = questions["items"][0]
    tagged = data(client.get(P + "/questions", params={"knowledge_point_id": target["knowledge_point_ids"][0]}))
    assert all(target["knowledge_point_ids"][0] in q["knowledge_point_ids"] for q in tagged["items"])
    error(client.get(P + "/questions", params={"knowledge_point_id": "KP-99"}), 404, "KNOWLEDGE_NOT_FOUND")


def test_grading_uses_server_key_and_rejects_forged_correctness(client, login):
    headers = login()
    q = question(client)
    body = answer_body(q, correct=False)
    rejected = client.post(P + "/learning/answers", headers=headers, json={**body, "is_correct": True})
    error(rejected, 422, "VALIDATION_ERROR")
    result = data(client.post(P + "/learning/answers", headers=headers, json=body), 201)
    assert result["record"]["is_correct"] is False
    assert result["correct_option"] == q["correct"]
    assert result["record"]["knowledge_point_ids"] == q["points"]
    assert result["explanation"] and result["replayed"] is False


def test_answer_idempotency_and_event_conflict(client, login):
    headers = login()
    q = question(client)
    body = answer_body(q)
    first = data(client.post(P + "/learning/answers", headers=headers, json=body), 201)
    replay = data(client.post(P + "/learning/answers", headers=headers, json=body), 201)
    assert first["record"]["id"] == replay["record"]["id"] and replay["replayed"] is True
    error(client.post(P + "/learning/answers", headers=headers, json=answer_body(q, correct=False)), 409, "EVENT_CONFLICT")
    assert data(client.get(P + "/learning/records", headers=headers))["total"] == 1


def test_simultaneous_duplicate_answers_create_only_one_record(client, login):
    headers = login()
    body = answer_body(question(client), event="simultaneous-event")
    def submit(_):
        return client.post(P + "/learning/answers", headers=headers, json=body)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [data(r, 201) for r in executor.map(submit, range(4))]
    assert len({r["record"]["id"] for r in results}) == 1
    assert sum(not r["replayed"] for r in results) == 1
    assert data(client.get(P + "/learning/records", headers=headers))["total"] == 1


def test_learning_records_are_isolated_by_authenticated_user(client, login):
    new_headers, student_headers = login(), login("demo_student")
    assert data(client.get(P + "/learning/records", headers=new_headers))["total"] == 0
    before = data(client.get(P + "/learning/records", headers=student_headers))["total"]
    assert before > 0
    data(client.post(P + "/learning/answers", headers=new_headers, json=answer_body(question(client))), 201)
    assert data(client.get(P + "/learning/records", headers=new_headers))["total"] == 1
    assert data(client.get(P + "/learning/records", headers=student_headers))["total"] == before


def test_new_student_has_unknown_mastery_and_five_initial_questions(client, login):
    result = data(client.get(P + "/learning/diagnosis", headers=login()))
    assert result["total_records"] == 0 and result["weak_points"] == []
    assert len(result["mastery"]) == 60 and len(result["recommended_questions"]) == 5
    assert all(p["mastery"] is None and p["error_rate"] is None and p["attempts"] == 0 for p in result["mastery"])
    assert "correct_option" not in str(result)


def test_weakest_three_are_ordered_by_real_error_rates(client, login):
    result = data(client.get(P + "/learning/diagnosis", headers=login("demo_weak")))
    assert [p["knowledge_point_id"] for p in result["weak_points"]] == ["KP-33", "KP-34", "KP-48"]
    assert [(p["errors"], p["attempts"]) for p in result["weak_points"]] == [(5, 5), (4, 5), (3, 5)]
    assert [p["error_rate"] for p in result["weak_points"]] == [1.0, 0.8, 0.6]
    assert len(result["recommended_questions"]) == 5
    assert result["recommended_questions"][0]["knowledge_point_ids"] == ["KP-33"]
    filtered = data(client.get(P + "/learning/diagnosis", headers=login("demo_weak"), params={"min_attempts": 6}))
    assert filtered["weak_points"] == []


def test_error_rate_ties_use_attempt_count_then_knowledge_id(client, login):
    headers = login()
    first, second = question(client, "Q-01"), question(client, "Q-02")
    assert first["points"] != second["points"]
    for q, event in ((first, "tie-event-01"), (second, "tie-event-02")):
        data(client.post(P + "/learning/answers", headers=headers, json=answer_body(q, event, correct=False)), 201)
    tied = data(client.get(P + "/learning/diagnosis", headers=headers))["weak_points"]
    assert [p["knowledge_point_id"] for p in tied] == sorted(first["points"] + second["points"])
    data(client.post(P + "/learning/answers", headers=headers, json=answer_body(second, "tie-event-03", correct=False)), 201)
    result = data(client.get(P + "/learning/diagnosis", headers=headers))["weak_points"]
    assert result[0]["knowledge_point_id"] == second["points"][0]
    assert result[0]["attempts"] == 2 and result[0]["error_rate"] == 1


def test_diagram_versions_preserve_positions_and_properties(client, login, graph):
    headers = login()
    first = data(client.post(P + "/diagrams", headers=headers, json=graph), 201)
    identifier = first["diagram_id"]
    changed = copy.deepcopy(graph)
    changed["data"]["nodes"][0]["position"] = {"x": 79, "y": 120}
    changed["title"] = "第二版"
    second = data(client.put(P + f"/diagrams/{identifier}", headers=headers, json={**changed, "base_version": 1}))
    assert second["version"] == 2
    current = data(client.get(P + f"/diagrams/{identifier}", headers=headers))
    original = data(client.get(P + f"/diagrams/{identifier}", headers=headers, params={"version": 1}))
    assert current["data"]["nodes"][0]["position"] == {"x": 79, "y": 120}
    assert original["data"]["nodes"][0]["position"] == {"x": 20, "y": 30}
    assert original["data"]["nodes"][0]["properties"] == {"attributes": ["isbn: String"]}
    versions = data(client.get(P + f"/diagrams/{identifier}/versions", headers=headers))
    assert versions["total"] == 2 and [v["version"] for v in versions["items"]] == [2, 1]
    listed = data(client.get(P + "/diagrams", headers=headers))
    assert listed["total"] == 1 and listed["items"][0]["version"] == 2


def test_diagram_stale_save_reports_latest_version(client, login, graph):
    headers = login()
    identifier = data(client.post(P + "/diagrams", headers=headers, json=graph), 201)["diagram_id"]
    body = {**graph, "base_version": 1}
    data(client.put(P + f"/diagrams/{identifier}", headers=headers, json=body))
    conflict = error(client.put(P + f"/diagrams/{identifier}", headers=headers, json=body), 409, "VERSION_CONFLICT")
    assert conflict["data"]["latest_version"] == 2
    assert data(client.get(P + f"/diagrams/{identifier}/versions", headers=headers))["total"] == 2


def test_simultaneous_diagram_saves_accept_only_one_new_version(client, login, graph):
    headers = login()
    identifier = data(client.post(P + "/diagrams", headers=headers, json=graph), 201)["diagram_id"]
    def save(index):
        return client.put(P + f"/diagrams/{identifier}", headers=headers, json={**graph, "title": f"修改 {index}", "base_version": 1})
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert data(client.get(P + f"/diagrams/{identifier}/versions", headers=headers))["total"] == 2


@pytest.mark.parametrize("invalid", ["duplicate_node", "edge_id_equals_node", "unknown_endpoint"])
def test_graph_rejects_invalid_references_and_ids(client, login, graph, invalid):
    if invalid == "duplicate_node":
        graph["data"]["nodes"][1]["id"] = "book"
    elif invalid == "edge_id_equals_node":
        graph["data"]["edges"][0]["id"] = "book"
    else:
        graph["data"]["edges"][0]["target"] = "missing"
    error(client.post(P + "/diagrams", headers=login(), json=graph), 422, "VALIDATION_ERROR")


def test_diagram_types_cannot_change_and_other_users_cannot_read_or_write(client, login, graph):
    owner, other = login(), login("demo_student")
    identifier = data(client.post(P + "/diagrams", headers=owner, json=graph), 201)["diagram_id"]
    error(client.put(P + f"/diagrams/{identifier}", headers=owner, json={**graph, "base_version": 1, "diagram_type": "sequence"}), 422, "DIAGRAM_TYPE_IMMUTABLE")
    for path in (f"/diagrams/{identifier}", f"/diagrams/{identifier}/versions"):
        error(client.get(P + path, headers=other), 404, "DIAGRAM_NOT_FOUND")
    error(client.put(P + f"/diagrams/{identifier}", headers=other, json={**graph, "base_version": 1}), 404, "DIAGRAM_NOT_FOUND")
    error(client.get(P + "/diagrams/does-not-exist", headers=owner), 404, "DIAGRAM_NOT_FOUND")


def test_upload_download_metadata_and_ownership(client, login):
    owner, other = login(), login("demo_student")
    content = b"%PDF-1.7\nprivate example content\n%%EOF"
    item = data(client.post(P + "/documents", headers=owner, files={"file": ("report.pdf", content, "application/pdf")}), 201)
    assert item["sha256"] == hashlib.sha256(content).hexdigest()
    assert item["size_bytes"] == len(content) and item["status"] == "uploaded"
    assert "storage_key" not in item and "user_id" not in item
    assert data(client.get(P + "/documents", headers=owner))["total"] == 1
    assert data(client.get(P + "/documents", headers=other))["total"] == 0
    path = P + f"/documents/{item['id']}"
    assert data(client.get(path, headers=owner)) == item
    download = client.get(path + "/content", headers=owner)
    assert download.status_code == 200 and download.content == content
    assert "attachment" in download.headers["content-disposition"]
    error(client.get(path, headers=other), 404, "DOCUMENT_NOT_FOUND")
    error(client.get(path + "/content", headers=other), 404, "DOCUMENT_NOT_FOUND")


@pytest.mark.parametrize("name,content,code", [("script.py", b"print(1)", "FILE_TYPE_UNSUPPORTED"), ("fake.pdf", b"not a pdf", "FILE_CONTENT_INVALID"), ("empty.pdf", b"", "FILE_CONTENT_INVALID"), ("fake.docx", b"PK-not-really-zip", "FILE_CONTENT_INVALID")])
def test_upload_rejects_unsupported_or_mismatched_files_and_cleans_storage(client, login, name, content, code):
    error(client.post(P + "/documents", headers=login(), files={"file": (name, content)}), 415, code)
    assert list(client.app.state.settings.upload_dir.iterdir()) == []


def test_upload_accepts_docx_and_zip_without_extracting_archive(client, login):
    headers = login()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    result = data(client.post(P + "/documents", headers=headers, files={"file": ("document.docx", buffer.getvalue())}), 201)
    assert result["original_name"] == "document.docx"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("../untrusted.txt", "this entry must not be extracted")
    data(client.post(P + "/documents", headers=headers, files={"file": ("data.zip", archive_bytes.getvalue())}), 201)
    assert len(list(client.app.state.settings.upload_dir.iterdir())) == 2
    assert not (client.app.state.settings.upload_dir.parent / "untrusted.txt").exists()


def test_upload_size_boundary_and_cleanup(client, login):
    client.app.state.settings.max_upload_bytes = 128
    content = b"%PDF-1.7" + b"x" * 120
    assert len(content) == 128
    data(client.post(P + "/documents", headers=login(), files={"file": ("at-limit.pdf", content)}), 201)
    error(client.post(P + "/documents", headers=login(), files={"file": ("too-large.pdf", content + b"x")}), 413, "FILE_TOO_LARGE")
    assert len(list(client.app.state.settings.upload_dir.iterdir())) == 1


def test_default_upload_limit_is_twenty_mebibytes(client):
    assert client.app.state.settings.max_upload_bytes == 20 * 1024 * 1024
