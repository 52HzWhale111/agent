from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id():
    return str(uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    chapter: Mapped[int] = mapped_column(Integer, index=True)
    definition: Mapped[str] = mapped_column(Text)
    example: Mapped[str] = mapped_column(Text)
    common_mistake: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    difficulty: Mapped[str] = mapped_column(String(16))
    related_features: Mapped[list] = mapped_column(JSON, default=list)
    source_file: Mapped[str] = mapped_column(Text)


class Exercise(Base):
    __tablename__ = "exercises"
    __table_args__ = (CheckConstraint("kind IN ('case','question')", name="ck_exercise_kind"),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(200))
    knowledge_point_ids: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[dict] = mapped_column(JSON)
    answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_file: Mapped[str] = mapped_column(Text)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (CheckConstraint("size_bytes > 0", name="ck_document_size"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningRecord(Base):
    __tablename__ = "learning_records"
    __table_args__ = (
        UniqueConstraint("user_id", "client_event_id", name="uq_record_event"),
        Index("ix_records_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"))
    knowledge_point_ids: Mapped[list] = mapped_column(JSON)
    submitted_answer: Mapped[dict] = mapped_column(JSON)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    activity_type: Mapped[str] = mapped_column(String(32), default="practice")
    client_event_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Diagram(Base):
    __tablename__ = "diagrams"
    __table_args__ = (
        UniqueConstraint("diagram_id", "version", name="uq_diagram_version"),
        CheckConstraint("version >= 1", name="ck_diagram_version"),
        CheckConstraint("diagram_type IN ('class','use_case','sequence')", name="ck_diagram_type"),
        Index("ix_diagrams_owner_group", "user_id", "diagram_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagram_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    diagram_type: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
