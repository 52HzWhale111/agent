import json
from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def utc_datetimes(cls, value):
        # SQLite drops tzinfo on read; all stored timestamps originate in UTC.
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Envelope(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = "成功"
    data: T
    request_id: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(BaseModel):
    code: str
    message: str
    data: Any = None
    request_id: str


class RegisterIn(Model):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return value.lower()

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value):
        if not value.strip():
            raise ValueError("昵称不能全为空白")
        return value.strip()


class LoginIn(Model):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserOut(Model):
    id: str
    username: str
    display_name: str
    created_at: datetime


class TokenOut(Model):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class KnowledgeOut(Model):
    id: str
    title: str
    chapter: int
    definition: str
    example: str
    common_mistake: str
    source: str
    tags: list[str]
    difficulty: str
    related_features: list[str]
    source_file: str


class ExerciseOut(Model):
    id: str
    kind: str
    title: str
    knowledge_point_ids: list[str]
    content: dict
    source_file: str


class CaseOut(ExerciseOut):
    answer_points: Any = None


class AnswerIn(Model):
    exercise_id: str = Field(min_length=1, max_length=16)
    answer: str = Field(pattern=r"^[A-Z]$")
    client_event_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class RecordOut(Model):
    id: str
    exercise_id: str
    knowledge_point_ids: list[str]
    submitted_answer: dict
    is_correct: bool
    activity_type: str
    client_event_id: str
    created_at: datetime


class AnswerOut(Model):
    record: RecordOut
    correct_option: str
    explanation: str
    replayed: bool


class MasteryOut(Model):
    knowledge_point_id: str
    title: str
    attempts: int
    errors: int
    error_rate: float | None
    mastery: float | None


class DiagnosisOut(Model):
    total_records: int
    correct_records: int
    weak_points: list[MasteryOut]
    mastery: list[MasteryOut]
    recommended_questions: list[ExerciseOut]
    recommendation_reason: str


class Position(Model):
    x: float
    y: float


class Node(Model):
    id: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=64)
    label: str = Field(max_length=500)
    position: Position
    properties: dict[str, Any] = Field(default_factory=dict)


class Edge(Model):
    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphData(Model):
    nodes: list[Node] = Field(max_length=500)
    edges: list[Edge] = Field(max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self):
        nodes = {n.id for n in self.nodes}
        all_ids = [n.id for n in self.nodes] + [e.id for e in self.edges]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("节点和连线ID必须唯一")
        if any(e.source not in nodes or e.target not in nodes for e in self.edges):
            raise ValueError("连线端点必须引用本图存在的节点")
        if len(json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False).encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("图数据不能超过2MiB")
        return self


class DiagramIn(Model):
    title: str = Field(min_length=1, max_length=200)
    diagram_type: Literal["class", "use_case", "sequence"]
    data: GraphData

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("标题不能全为空白")
        return value.strip()


class DiagramSaveIn(DiagramIn):
    base_version: int = Field(ge=1)


class DiagramOut(Model):
    diagram_id: str
    title: str
    diagram_type: str
    version: int
    data: GraphData
    created_at: datetime


class DiagramSummary(Model):
    diagram_id: str
    title: str
    diagram_type: str
    version: int
    created_at: datetime


class DocumentOut(Model):
    id: str
    original_name: str
    media_type: str
    size_bytes: int
    sha256: str
    status: str
    created_at: datetime


class HealthOut(Model):
    status: str
    database: str
