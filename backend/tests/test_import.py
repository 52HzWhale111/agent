import json
from collections import defaultdict
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Diagram, Exercise, KnowledgePoint, LearningRecord, User
from app.security import verify_password
from app.seed import DATA_DIR, find_knowledge_dir, load_knowledge_bundle, load_knowledge_snapshot, seed_database

KNOWLEDGE_DIR = find_knowledge_dir(Path(__file__).resolve().parents[2])


@pytest.fixture
def seed_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_import_is_complete_and_idempotent(seed_session):
    first = seed_database(seed_session, KNOWLEDGE_DIR)
    seed_session.commit()
    assert first == {"knowledge_points": 60, "cases": 20, "questions": 18}
    seed_database(seed_session, KNOWLEDGE_DIR)
    seed_session.commit()
    assert seed_session.scalar(select(func.count()).select_from(KnowledgePoint)) == 60
    assert seed_session.scalar(select(func.count()).select_from(Exercise)) == 38
    assert seed_session.scalar(select(func.count()).select_from(User)) == 0
    assert seed_session.get(KnowledgePoint, "KP-59").source == "课件17.pdf。"
    assert seed_session.get(KnowledgePoint, "KP-01").related_features == ["概念问答"]
    assert seed_session.get(KnowledgePoint, "KP-09").related_features == []
    case = seed_session.get(Exercise, "RC-01")
    assert case.knowledge_point_ids == []
    assert "重复认领" in case.content["raw_markdown"]
    assert "管理员审核" in case.answer["key_points"]


def test_demo_accounts_statistics_and_versions_are_stable(seed_session):
    counts = seed_database(seed_session, KNOWLEDGE_DIR, include_demo=True)
    seed_session.commit()
    assert counts["demo_records"] == 45
    weak = seed_session.scalar(select(User).where(User.username == "demo_weak"))
    new = seed_session.scalar(select(User).where(User.username == "demo_new"))
    assert verify_password("Demo@2026!", weak.password_hash)
    original_password = weak.password_hash
    seed_database(seed_session, KNOWLEDGE_DIR, include_demo=True)
    seed_session.commit()
    assert weak.password_hash == original_password
    assert seed_session.scalar(select(func.count()).select_from(User)) == 3
    assert seed_session.scalar(select(func.count()).select_from(LearningRecord)) == 45
    assert seed_session.scalar(select(func.count()).select_from(Diagram)) == 4
    assert seed_session.scalar(select(func.count()).select_from(LearningRecord).where(LearningRecord.user_id == new.id)) == 0
    stats = defaultdict(lambda: [0, 0])
    for record in seed_session.scalars(select(LearningRecord).where(LearningRecord.user_id == weak.id)):
        for point_id in record.knowledge_point_ids:
            stats[point_id][0] += 1
            stats[point_id][1] += int(not record.is_correct)
    ranked = sorted(stats, key=lambda point_id: (-stats[point_id][1] / stats[point_id][0], point_id))
    assert ranked[:3] == ["KP-33", "KP-34", "KP-48"]
    assert [stats[point_id] for point_id in ranked[:3]] == [[5, 5], [5, 4], [5, 3]]
    versions = list(seed_session.scalars(select(Diagram).where(Diagram.diagram_type == "class").order_by(Diagram.version)))
    assert [diagram.version for diagram in versions] == [1, 2]
    assert versions[0].diagram_id == versions[1].diagram_id
    assert len(versions[0].data["nodes"]) == 3
    assert len(versions[1].data["nodes"]) == 4


def test_demo_graphs_have_unique_ids_and_valid_edge_endpoints():
    payload = json.loads((DATA_DIR / "demo_diagrams.json").read_text(encoding="utf-8"))
    assert {diagram["diagram_type"] for diagram in payload["diagrams"]} == {"use_case", "class", "sequence"}
    for diagram in payload["diagrams"]:
        nodes, edges = diagram["data"]["nodes"], diagram["data"]["edges"]
        node_ids = {node["id"] for node in nodes}
        edge_ids = {edge["id"] for edge in edges}
        assert len(node_ids) == len(nodes)
        assert len(edge_ids) == len(edges)
        assert not node_ids & edge_ids
        assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in edges)


def test_import_fails_clearly_for_missing_bundle(tmp_path):
    with pytest.raises(FileNotFoundError, match="01_知识点"):
        load_knowledge_bundle(tmp_path)


def test_snapshot_supports_checkout_without_separate_knowledge_pr():
    points, cases = load_knowledge_snapshot()
    assert len(points) == 60 and len(cases) == 20
    assert points[0]["id"] == "KP-01" and cases[-1]["id"] == "RC-20"
    assert points[0]["source"]


def test_knowledge_discovery_supports_team_and_downloaded_layouts(tmp_path):
    assert find_knowledge_dir(tmp_path) is None
    outer = tmp_path / "knowledge-base"
    inner = outer / "knowledge-base"
    for section in ("01_知识点", "02_需求案例"):
        (inner / section).mkdir(parents=True)
    assert find_knowledge_dir(tmp_path) == inner
    for section in ("01_知识点", "02_需求案例"):
        (outer / section).mkdir()
    assert find_knowledge_dir(tmp_path) == outer
