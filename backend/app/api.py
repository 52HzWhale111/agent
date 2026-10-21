import hashlib
import logging
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import schemas as s
from .config import Settings
from .database import Base, get_db, make_database
from .models import Diagram, Document, Exercise, KnowledgePoint, LearningRecord, User
from .security import hash_password, issue_token, read_token, verify_password

log = logging.getLogger("zhigou")
bearer = HTTPBearer(auto_error=False)
Db = Annotated[Session, Depends(get_db)]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, data=None):
        self.status, self.code, self.message, self.data = status, code, message, data


def ok(request: Request, data, status=200):
    return {"code": "OK", "message": "成功", "data": data, "request_id": request.state.request_id}


def current_user(request: Request, db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    try:
        user_id = read_token(credentials.credentials, request.app.state.settings)
    except (jwt.PyJWTError, KeyError, TypeError):
        raise ApiError(401, "TOKEN_INVALID", "登录已过期或凭证无效，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(401, "TOKEN_INVALID", "登录凭证对应的用户不存在")
    return user


Me = Annotated[User, Depends(current_user)]
ERRORS = {code: {"model": s.ErrorBody} for code in (400, 401, 404, 409, 413, 415, 422, 500, 503)}


def page_result(db, query, page, page_size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {"items": list(db.scalars(query.offset((page - 1) * page_size).limit(page_size))), "total": total, "page": page, "page_size": page_size}


def owned_diagram(db, user_id, diagram_id, version=None):
    query = select(Diagram).where(Diagram.user_id == user_id, Diagram.diagram_id == diagram_id)
    if version is not None:
        query = query.where(Diagram.version == version)
    result = db.scalar(query.order_by(Diagram.version.desc()).limit(1))
    if result is None:
        raise ApiError(404, "DIAGRAM_NOT_FOUND", "图或版本不存在")
    return result


def owned_document(db, user_id, document_id):
    result = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user_id))
    if result is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    return result


def exercise_public(exercise):
    return s.ExerciseOut.model_validate(exercise)


def answer_result(record, exercise, replayed):
    return {"record": record, "correct_option": exercise.answer["correct_option"], "explanation": exercise.answer["explanation"], "replayed": replayed}


def check_replay(existing, body, exercise):
    if existing.exercise_id != body.exercise_id or existing.submitted_answer != {"option": body.answer}:
        raise ApiError(409, "EVENT_CONFLICT", "该答题事件号已用于其他内容，请换一个事件号")
    return answer_result(existing, exercise, True)


def diagnose(db, user_id, min_attempts):
    points = list(db.scalars(select(KnowledgePoint).order_by(KnowledgePoint.id)))
    records = list(db.scalars(select(LearningRecord).where(LearningRecord.user_id == user_id)))
    stats = {p.id: {"knowledge_point_id": p.id, "title": p.title, "attempts": 0, "errors": 0} for p in points}
    for record in records:
        for point_id in set(record.knowledge_point_ids):
            if point_id in stats:
                stats[point_id]["attempts"] += 1
                stats[point_id]["errors"] += int(not record.is_correct)
    for value in stats.values():
        attempts = value["attempts"]
        value["error_rate"] = value["errors"] / attempts if attempts else None
        value["mastery"] = (attempts - value["errors"]) / attempts if attempts else None
    studied = [v for v in stats.values() if v["attempts"] >= min_attempts]
    weak = sorted(studied, key=lambda v: (-v["error_rate"], -v["attempts"], v["knowledge_point_id"]))[:3]
    priority = {v["knowledge_point_id"]: i for i, v in enumerate(weak)}
    answered = {r.exercise_id for r in records}
    questions = list(db.scalars(select(Exercise).where(Exercise.kind == "question").order_by(Exercise.id)))
    def ranking(q):
        match = min((priority[k] for k in q.knowledge_point_ids if k in priority), default=999)
        return (match, q.id in answered, q.id)
    recommendations = sorted(questions, key=ranking)[:5]
    return {"total_records": len(records), "correct_records": sum(r.is_correct for r in records), "weak_points": weak, "mastery": list(stats.values()), "recommended_questions": [exercise_public(q) for q in recommendations], "recommendation_reason": "优先覆盖薄弱知识点，同优先级先推荐未答题；不足5道时补充其他课程练习。" if weak else "暂无足够答题记录，先推荐课程基础练习；未学习不计为薄弱。"}


def validate_file(path, suffix):
    with path.open("rb") as f:
        head = f.read(8)
    if suffix == ".pdf":
        return head.startswith(b"%PDF-")
    if suffix == ".doc":
        return head == bytes.fromhex("D0CF11E0A1B11AE1")
    if suffix in (".docx", ".zip"):
        try:
            with zipfile.ZipFile(path) as archive:
                # Only inspect the central directory; never extract or run uploaded content.
                names = set(archive.namelist())
                return bool(names) and (suffix == ".zip" or {"[Content_Types].xml", "word/document.xml"} <= names)
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    settings.prepare()
    engine, factory = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        # Initial release only. Future schema changes require explicit migration scripts.
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="知构业务接口", version="1.0.0", description="后端1业务服务：账号、课程资料、文档、UML版本、答题与学情。Agent调用与异步任务由gateway提供。", lifespan=lifespan, responses=ERRORS)
    app.state.settings, app.state.engine, app.state.session_factory = settings, engine, factory
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request, status, code, message, data=None):
        return JSONResponse(status_code=status, content=jsonable_encoder({"code": code, "message": message, "data": data, "request_id": getattr(request.state, "request_id", "unknown")}), headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message, exc.data)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = [{"field": ".".join(map(str, error["loc"])), "reason": error["msg"], "type": error["type"]} for error in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数不符合接口约定", fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "接口不存在" if exc.status_code == 404 else str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        log.error("请求处理失败 request_id=%s", getattr(request.state, "request_id", "unknown"), exc_info=exc)
        response = error_response(request, 500, "INTERNAL_ERROR", "服务暂时异常，请稍后重试")
        response.headers["X-Request-ID"] = getattr(request.state, "request_id", "unknown")
        return response

    @app.get("/health", response_model=s.Envelope[s.HealthOut], tags=["健康检查"])
    def health(request: Request, db: Db):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise ApiError(503, "DATABASE_UNAVAILABLE", "数据库暂不可用")
        return ok(request, {"status": "ok", "database": "ok"})

    router = APIRouter(prefix="/api/v1")

    @router.post("/auth/register", response_model=s.Envelope[s.UserOut], status_code=201, tags=["认证"], summary="注册学生账号")
    def register(body: s.RegisterIn, request: Request, db: Db):
        user = User(username=body.username, display_name=body.display_name, password_hash=hash_password(body.password))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, "USERNAME_EXISTS", "用户名已被注册")
        return ok(request, user)

    @router.post("/auth/login", response_model=s.Envelope[s.TokenOut], tags=["认证"], summary="用JSON用户名密码登录")
    def login(body: s.LoginIn, request: Request, db: Db):
        user = db.scalar(select(User).where(User.username == body.username.lower()))
        dummy = request.app.state.dummy_password_hash
        verified = verify_password(body.password, user.password_hash if user else dummy)
        if not user or not verified:
            raise ApiError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
        return ok(request, {"access_token": issue_token(user.id, settings), "token_type": "bearer", "expires_in": settings.access_token_minutes * 60, "user": user})

    @router.get("/auth/me", response_model=s.Envelope[s.UserOut], tags=["认证"])
    def me(request: Request, user: Me):
        return ok(request, user)

    @router.get("/knowledge-points", response_model=s.Envelope[s.Page[s.KnowledgeOut]], tags=["课程资料"])
    def knowledge_points(request: Request, db: Db, chapter: int | None = Query(None, ge=1, le=8), keyword: str | None = Query(None, max_length=100), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        query = select(KnowledgePoint).order_by(KnowledgePoint.id)
        if chapter is not None:
            query = query.where(KnowledgePoint.chapter == chapter)
        if keyword:
            query = query.where(KnowledgePoint.title.contains(keyword, autoescape=True) | KnowledgePoint.definition.contains(keyword, autoescape=True))
        return ok(request, page_result(db, query, page, page_size))

    @router.get("/knowledge-points/{point_id}", response_model=s.Envelope[s.KnowledgeOut], tags=["课程资料"])
    def knowledge_detail(point_id: str, request: Request, db: Db):
        point = db.get(KnowledgePoint, point_id)
        if not point:
            raise ApiError(404, "KNOWLEDGE_NOT_FOUND", "知识点不存在")
        return ok(request, point)

    @router.get("/cases", response_model=s.Envelope[s.Page[s.ExerciseOut]], tags=["课程资料"])
    def cases(request: Request, db: Db, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return ok(request, page_result(db, select(Exercise).where(Exercise.kind == "case").order_by(Exercise.id), page, page_size))

    @router.get("/cases/{case_id}", response_model=s.Envelope[s.CaseOut], tags=["课程资料"])
    def case_detail(case_id: str, request: Request, db: Db):
        case = db.get(Exercise, case_id)
        if not case or case.kind != "case":
            raise ApiError(404, "CASE_NOT_FOUND", "案例不存在")
        result = exercise_public(case).model_dump()
        result["answer_points"] = case.answer
        return ok(request, result)

    @router.get("/questions", response_model=s.Envelope[s.Page[s.ExerciseOut]], tags=["练习与学情"])
    def questions(request: Request, db: Db, knowledge_point_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        rows = list(db.scalars(select(Exercise).where(Exercise.kind == "question").order_by(Exercise.id)))
        if knowledge_point_id:
            if not db.get(KnowledgePoint, knowledge_point_id):
                raise ApiError(404, "KNOWLEDGE_NOT_FOUND", "知识点不存在")
            rows = [q for q in rows if knowledge_point_id in q.knowledge_point_ids]
        start = (page - 1) * page_size
        return ok(request, {"items": [exercise_public(q) for q in rows[start:start + page_size]], "total": len(rows), "page": page, "page_size": page_size})

    @router.post("/learning/answers", response_model=s.Envelope[s.AnswerOut], status_code=201, tags=["练习与学情"], summary="由服务端判分并幂等保存答题")
    def submit_answer(body: s.AnswerIn, request: Request, db: Db, user: Me):
        exercise = db.get(Exercise, body.exercise_id)
        if not exercise or exercise.kind != "question":
            raise ApiError(404, "QUESTION_NOT_FOUND", "可评分的练习题不存在")
        if body.answer not in {option["id"] for option in exercise.content["options"]}:
            raise ApiError(422, "ANSWER_INVALID", "答案不是本题的有效选项")
        query = select(LearningRecord).where(LearningRecord.user_id == user.id, LearningRecord.client_event_id == body.client_event_id)
        existing = db.scalar(query)
        if existing:
            return ok(request, check_replay(existing, body, exercise))
        record = LearningRecord(user_id=user.id, exercise_id=exercise.id, knowledge_point_ids=list(exercise.knowledge_point_ids), submitted_answer={"option": body.answer}, is_correct=body.answer == exercise.answer["correct_option"], activity_type="practice", client_event_id=body.client_event_id)
        db.add(record)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(query)
            if existing:
                return ok(request, check_replay(existing, body, exercise))
            raise
        return ok(request, answer_result(record, exercise, False))

    @router.get("/learning/records", response_model=s.Envelope[s.Page[s.RecordOut]], tags=["练习与学情"])
    def records(request: Request, db: Db, user: Me, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return ok(request, page_result(db, select(LearningRecord).where(LearningRecord.user_id == user.id).order_by(LearningRecord.created_at.desc(), LearningRecord.id), page, page_size))

    @router.get("/learning/diagnosis", response_model=s.Envelope[s.DiagnosisOut], tags=["练习与学情"])
    def diagnosis(request: Request, db: Db, user: Me, min_attempts: int = Query(1, ge=1, le=1000)):
        return ok(request, diagnose(db, user.id, min_attempts))

    @router.post("/diagrams", response_model=s.Envelope[s.DiagramOut], status_code=201, tags=["UML图"])
    def create_diagram(body: s.DiagramIn, request: Request, db: Db, user: Me):
        diagram = Diagram(diagram_id=str(uuid4()), user_id=user.id, title=body.title, diagram_type=body.diagram_type, version=1, data=body.data.model_dump())
        db.add(diagram)
        db.commit()
        return ok(request, diagram)

    @router.get("/diagrams", response_model=s.Envelope[s.Page[s.DiagramSummary]], tags=["UML图"], summary="每张图只列出最新版本")
    def diagrams(request: Request, db: Db, user: Me, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        latest = select(Diagram.diagram_id, func.max(Diagram.version).label("latest_version")).where(Diagram.user_id == user.id).group_by(Diagram.diagram_id).subquery()
        query = select(Diagram).join(latest, (Diagram.diagram_id == latest.c.diagram_id) & (Diagram.version == latest.c.latest_version)).where(Diagram.user_id == user.id).order_by(Diagram.created_at.desc(), Diagram.diagram_id)
        return ok(request, page_result(db, query, page, page_size))

    @router.get("/diagrams/{diagram_id}", response_model=s.Envelope[s.DiagramOut], tags=["UML图"])
    def read_diagram(diagram_id: str, request: Request, db: Db, user: Me, version: int | None = Query(None, ge=1)):
        return ok(request, owned_diagram(db, user.id, diagram_id, version))

    @router.get("/diagrams/{diagram_id}/versions", response_model=s.Envelope[s.Page[s.DiagramSummary]], tags=["UML图"])
    def diagram_versions(diagram_id: str, request: Request, db: Db, user: Me, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        owned_diagram(db, user.id, diagram_id)
        return ok(request, page_result(db, select(Diagram).where(Diagram.user_id == user.id, Diagram.diagram_id == diagram_id).order_by(Diagram.version.desc()), page, page_size))

    @router.put("/diagrams/{diagram_id}", response_model=s.Envelope[s.DiagramOut], tags=["UML图"], summary="以base_version检查版本冲突并追加新版本")
    def save_diagram(diagram_id: str, body: s.DiagramSaveIn, request: Request, db: Db, user: Me):
        latest = owned_diagram(db, user.id, diagram_id)
        if latest.version != body.base_version:
            raise ApiError(409, "VERSION_CONFLICT", "图已被更新，请读取最新版本后再保存", {"latest_version": latest.version})
        if latest.diagram_type != body.diagram_type:
            raise ApiError(422, "DIAGRAM_TYPE_IMMUTABLE", "同一张图的类型不能修改，请新建图")
        diagram = Diagram(diagram_id=diagram_id, user_id=user.id, title=body.title, diagram_type=body.diagram_type, version=latest.version + 1, data=body.data.model_dump())
        db.add(diagram)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            latest = owned_diagram(db, user.id, diagram_id)
            raise ApiError(409, "VERSION_CONFLICT", "另一请求已保存，请读取最新版本后再保存", {"latest_version": latest.version})
        return ok(request, diagram)

    @router.post("/documents", response_model=s.Envelope[s.DocumentOut], status_code=201, tags=["文档"], summary="上传doc/docx/pdf/zip 最大20MiB")
    async def upload_document(request: Request, db: Db, user: Me, file: UploadFile = File(...)):
        original = Path((file.filename or "").replace("\\", "/")).name
        suffix = Path(original).suffix.lower()
        if len(original) > 255 or not original or any(ord(c) < 32 for c in original):
            raise ApiError(422, "FILENAME_INVALID", "文件名无效或过长")
        media_types = {".pdf": "application/pdf", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".zip": "application/zip"}
        if suffix not in media_types:
            raise ApiError(415, "FILE_TYPE_UNSUPPORTED", "只支持doc、docx、pdf、zip文件")
        storage_key = f"{uuid4().hex}{suffix}"
        path = settings.upload_dir / storage_key
        digest, size, persisted = hashlib.sha256(), 0, False
        try:
            with path.open("xb") as output:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise ApiError(413, "FILE_TOO_LARGE", f"单个文件不能超过{settings.max_upload_bytes // (1024 * 1024)}MiB")
                    output.write(chunk)
                    digest.update(chunk)
            if not size or not validate_file(path, suffix):
                raise ApiError(415, "FILE_CONTENT_INVALID", "文件为空、已损坏或内容与扩展名不匹配")
            document = Document(user_id=user.id, original_name=original, storage_key=storage_key, media_type=media_types[suffix], size_bytes=size, sha256=digest.hexdigest(), status="uploaded")
            db.add(document)
            db.commit()
            persisted = True
            return ok(request, document)
        finally:
            await file.close()
            if not persisted:
                path.unlink(missing_ok=True)

    @router.get("/documents", response_model=s.Envelope[s.Page[s.DocumentOut]], tags=["文档"])
    def documents(request: Request, db: Db, user: Me, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return ok(request, page_result(db, select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc(), Document.id), page, page_size))

    @router.get("/documents/{document_id}", response_model=s.Envelope[s.DocumentOut], tags=["文档"])
    def document_detail(document_id: str, request: Request, db: Db, user: Me):
        return ok(request, owned_document(db, user.id, document_id))

    @router.get("/documents/{document_id}/content", response_class=FileResponse, tags=["文档"], responses={200: {"description": "通过鉴权后下载原文件 二进制"}})
    def download_document(document_id: str, db: Db, user: Me):
        document = owned_document(db, user.id, document_id)
        path = settings.upload_dir / document.storage_key
        if not path.is_file():
            raise ApiError(404, "DOCUMENT_FILE_MISSING", "原文件暂不可用，请联系维护人员")
        return FileResponse(path, filename=document.original_name, media_type=document.media_type)

    app.include_router(router)
    app.state.dummy_password_hash = hash_password(str(uuid4()))
    return app
