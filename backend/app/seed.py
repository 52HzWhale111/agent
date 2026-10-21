"""Import the supplied Markdown bundle and optional, explicitly labelled demo data.

No import runs at module import time. The caller owns the transaction and should
commit after this function returns. Source mistakes are retained for review,
rather than being silently rewritten by the importer.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Diagram, Exercise, KnowledgePoint, LearningRecord, User
from .security import hash_password

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEMO_NAMESPACE = UUID("ca65431c-dc45-42ab-8abc-64090a645bd0")
DEMO_PASSWORD = "Demo@2026!"
RECORD_PATTERN = re.compile(
    r"^## (?P<id>(?:KP|RC)-\d{2}) (?P<title>[^\r\n]+)\r?\n"
    r"(?P<body>.*?)(?=^## (?:KP|RC)-\d{2} |\Z)",
    re.MULTILINE | re.DOTALL,
)
FIELD_PATTERN = re.compile(r"^- \*\*(.+?)\*\*：([^\r\n]+)", re.MULTILINE)


def _stable_id(value: str) -> str:
    return str(uuid5(DEMO_NAMESPACE, value))


def _parts(value: str, separator: str = "、") -> list[str]:
    return [part.strip().rstrip("。") for part in value.split(separator) if part.strip().rstrip("。")]


def _records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    parsed = []
    for match in RECORD_PATTERN.finditer(text):
        fields = dict(FIELD_PATTERN.findall(match["body"]))
        parsed.append({
            "id": match["id"],
            "title": match["title"].strip(),
            "fields": fields,
            "raw_markdown": match.group(0).strip(),
        })
    if not parsed:
        raise ValueError(f"未找到知识点或案例记录：{path.name}")
    return parsed


def _require_fields(record: dict, required: set[str], path: Path) -> None:
    missing = required - record["fields"].keys()
    if missing:
        raise ValueError(f"{path.name} / {record['id']} 缺少字段：{', '.join(sorted(missing))}")


def _require_ids(records: list[dict], prefix: str, count: int) -> None:
    actual = [record["id"] for record in records]
    expected = {f"{prefix}-{number:02d}" for number in range(1, count + 1)}
    duplicates = [key for key, value in Counter(actual).items() if value > 1]
    if duplicates or set(actual) != expected:
        raise ValueError(
            f"{prefix}编号不完整或重复：缺少{sorted(expected - set(actual))}，"
            f"多余{sorted(set(actual) - expected)}，重复{duplicates}"
        )


def find_knowledge_dir(repo_root: Path) -> Path | None:
    """Prefer the team PR's layout; also support the original downloaded ZIP."""
    for candidate in (repo_root / "knowledge-base", repo_root / "knowledge-base" / "knowledge-base"):
        if (candidate / "01_知识点").is_dir() and (candidate / "02_需求案例").is_dir():
            return candidate
    return None


def load_knowledge_snapshot() -> tuple[list[dict], list[dict]]:
    """Allow a clean checkout to run before the separate knowledge PR is merged."""
    payload = json.loads((DATA_DIR / "knowledge_snapshot.json").read_text(encoding="utf-8"))
    knowledge_points, cases = payload["knowledge_points"], payload["cases"]
    _require_ids(knowledge_points, "KP", 60)
    _require_ids(cases, "RC", 20)
    return knowledge_points, cases


def load_knowledge_bundle(knowledge_dir: Path) -> tuple[list[dict], list[dict]]:
    """Validate the full input before changing database rows.

    ``knowledge_dir`` can be the inner bundle or its immediate parent. Course
    chapter comes from filenames; textbook chapters stay in the source string.
    """
    root = Path(knowledge_dir).resolve()
    if not (root / "01_知识点").is_dir() and (root / "knowledge-base" / "01_知识点").is_dir():
        root = root / "knowledge-base"
    if not (root / "01_知识点").is_dir() or not (root / "02_需求案例").is_dir():
        raise FileNotFoundError(f"知识库目录应包含 01_知识点 和 02_需求案例：{root}")

    knowledge_points, cases = [], []
    for path in sorted((root / "01_知识点").glob("*.md")):
        chapter_match = re.match(r"第(\d+)章", path.name)
        if not chapter_match:
            raise ValueError(f"知识点文件名缺少课程章编号：{path.name}")
        for record in _records(path):
            _require_fields(record, {"一句话定义", "生活例子", "学生常犯的错", "出处", "标签", "难度"}, path)
            fields = record["fields"]
            knowledge_points.append({
                "id": record["id"], "title": record["title"],
                "chapter": int(chapter_match[1]),
                "definition": fields["一句话定义"], "example": fields["生活例子"],
                "common_mistake": fields["学生常犯的错"], "source": fields["出处"],
                "tags": _parts(fields["标签"]), "difficulty": fields["难度"].rstrip("。"),
                "related_features": _parts(fields.get("关联功能", "")),
                "source_file": path.relative_to(root).as_posix(),
            })
    for path in sorted((root / "02_需求案例").glob("*.md")):
        for record in _records(path):
            _require_fields(record, {"背景", "角色", "功能需求", "非功能需求", "主流程", "异常流", "标准答案要点"}, path)
            fields = record["fields"]
            cases.append({
                "id": record["id"], "kind": "case", "title": record["title"],
                "knowledge_point_ids": [],
                "content": {
                    "background": fields["背景"], "roles": _parts(fields["角色"]),
                    "functional_requirements": _parts(fields["功能需求"]),
                    "nonfunctional_requirements": _parts(fields["非功能需求"], "；"),
                    "main_flow": _parts(fields["主流程"], "->"),
                    "exception_flows": _parts(fields["异常流"], "；"),
                    "raw_fields": fields, "raw_markdown": record["raw_markdown"],
                    "provenance": "团队提供的需求案例，未提供逐条外部出处或知识点映射",
                },
                "answer": {"key_points": fields["标准答案要点"]},
                "source_file": path.relative_to(root).as_posix(),
            })
    _require_ids(knowledge_points, "KP", 60)
    _require_ids(cases, "RC", 20)
    return knowledge_points, cases


def load_practice_questions(knowledge_ids: set[str]) -> list[dict]:
    payload = json.loads((DATA_DIR / "practice_questions.json").read_text(encoding="utf-8"))
    questions = payload["questions"]
    _require_ids(questions, "Q", 18)
    result = []
    for question in questions:
        if question["kind"] != "question":
            raise ValueError(f"{question['id']} 必须为 question")
        refs = question["knowledge_point_ids"]
        if not refs or len(refs) != len(set(refs)) or not set(refs) <= knowledge_ids:
            raise ValueError(f"{question['id']} 知识点引用无效")
        options = question["content"]["options"]
        ids = [option["id"] for option in options]
        if len(ids) != len(set(ids)) or set(ids) != {"A", "B", "C", "D"}:
            raise ValueError(f"{question['id']} 必须有唯一的 A B C D 选项")
        if question["answer"]["correct_option"] not in ids:
            raise ValueError(f"{question['id']} 正确选项无效")
        result.append({
            **question,
            "content": {**question["content"], "provenance": payload["provenance"]},
            "source_file": "backend/data/practice_questions.json",
        })
    return result


def _upsert(session: Session, model, values: dict):
    instance = session.get(model, values["id"])
    if instance is None:
        instance = model(**values)
        session.add(instance)
    else:
        for key, value in values.items():
            setattr(instance, key, value)
    return instance


def _seed_demo(session: Session, questions: list[dict]) -> dict:
    users = {}
    for username, display_name in [
        ("demo_new", "新生演示账号"),
        ("demo_student", "正常学习演示账号"),
        ("demo_weak", "薄弱点演示账号"),
    ]:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(id=_stable_id(f"user:{username}"), username=username,
                        display_name=display_name, password_hash=hash_password(DEMO_PASSWORD))
            session.add(user)
        users[username] = user
    session.flush()

    question_map = {question["id"]: question for question in questions}
    patterns = {
        "demo_student": {
            "Q-01": [True, True, True, False], "Q-02": [True, True, True, True],
            "Q-03": [True, False, True, True], "Q-04": [True, True, True, True],
            "Q-05": [True, True, True, False],
        },
        "demo_weak": {
            "Q-01": [False] * 5, "Q-02": [False, False, False, False, True],
            "Q-03": [False, False, False, True, True], "Q-04": [True] * 5,
            "Q-05": [True] * 5,
        },
    }
    base_time = datetime.now(timezone.utc) - timedelta(days=1)
    record_count = 0
    for username, exercises in patterns.items():
        for exercise_id, outcomes in exercises.items():
            question = question_map[exercise_id]
            correct_option = question["answer"]["correct_option"]
            wrong_option = next(option["id"] for option in question["content"]["options"]
                                if option["id"] != correct_option)
            for attempt, correct in enumerate(outcomes, 1):
                event_id = f"demo-v1-{exercise_id}-{attempt}"
                existing = session.scalar(select(LearningRecord).where(
                    LearningRecord.user_id == users[username].id,
                    LearningRecord.client_event_id == event_id,
                ))
                if existing is None:
                    session.add(LearningRecord(
                        id=_stable_id(f"record:{username}:{event_id}"),
                        user_id=users[username].id, exercise_id=exercise_id,
                        knowledge_point_ids=question["knowledge_point_ids"],
                        submitted_answer={"option": correct_option if correct else wrong_option},
                        is_correct=correct, activity_type="practice", client_event_id=event_id,
                        created_at=base_time + timedelta(minutes=record_count),
                    ))
                record_count += 1

    diagram_payload = json.loads((DATA_DIR / "demo_diagrams.json").read_text(encoding="utf-8"))
    for diagram in diagram_payload["diagrams"]:
        diagram_id = _stable_id(f"diagram:demo_student:{diagram['key']}")
        version = diagram["version"]
        existing = session.scalar(select(Diagram).where(
            Diagram.diagram_id == diagram_id, Diagram.version == version,
        ))
        if existing is None:
            data = {**diagram["data"], "metadata": {"provenance": diagram_payload["provenance"]}}
            session.add(Diagram(
                id=_stable_id(f"diagram-version:{diagram_id}:{version}"),
                diagram_id=diagram_id, user_id=users["demo_student"].id,
                title=diagram["title"], diagram_type=diagram["diagram_type"],
                version=version, data=data,
            ))
    session.flush()
    return {"demo_users": 3, "demo_records": record_count, "demo_diagram_versions": len(diagram_payload["diagrams"])}


def seed_database(session: Session, knowledge_dir: Path | None = None, include_demo: bool = False) -> dict:
    """Idempotently upsert 60 knowledge points, 20 cases and 18 practice questions.

    Demo users/records/diagrams are opt-in. Existing user passwords, submitted
    records and saved graph versions are never overwritten by demo seeding.
    This function does not commit; any validation failure can be rolled back.
    """
    selected_dir = knowledge_dir if knowledge_dir is not None else find_knowledge_dir(Path(__file__).resolve().parents[2])
    knowledge_points, cases = load_knowledge_bundle(selected_dir) if selected_dir is not None else load_knowledge_snapshot()
    questions = load_practice_questions({point["id"] for point in knowledge_points})
    for values in knowledge_points:
        _upsert(session, KnowledgePoint, values)
    for values in cases + questions:
        _upsert(session, Exercise, values)
    session.flush()
    result = {"knowledge_points": len(knowledge_points), "cases": len(cases), "questions": len(questions)}
    if include_demo:
        result.update(_seed_demo(session, questions))
    return result
