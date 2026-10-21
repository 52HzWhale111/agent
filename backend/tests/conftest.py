"""Integration fixtures use a temporary on-disk database and private upload directory."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.api import create_app
from app.config import Settings


@pytest.fixture
def client(tmp_path):
    from app.seed import seed_database

    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        secret_key="integration-test-key-is-private-and-at-least-32-characters",
        upload_dir=tmp_path / "uploads",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        with app.state.session_factory() as session:
            seed_database(session, include_demo=True)
            session.commit()
        yield test_client


@pytest.fixture
def login(client):
    def login_as(username="demo_new"):
        response = client.post("/api/v1/auth/login", json={"username": username, "password": "Demo@2026!"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

    return login_as


@pytest.fixture
def graph():
    return {
        "title": "图书借阅类图",
        "diagram_type": "class",
        "data": {
            "nodes": [
                {"id": "book", "type": "class", "label": "Book", "position": {"x": 20, "y": 30}, "properties": {"attributes": ["isbn: String"]}},
                {"id": "loan", "type": "class", "label": "Loan", "position": {"x": 300, "y": 30}},
            ],
            "edges": [{"id": "loan_book", "source": "loan", "target": "book", "type": "association", "label": "belongs to"}],
        },
    }
