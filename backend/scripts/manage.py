"""初始化数据库与导出可交接的接口及建表文件。"""
import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.api import create_app
from app.config import Settings
from app.database import Base, make_database
from app import models  # register all six tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="建表并幂等导入资料")
    init.add_argument("--demo", action="store_true", help="显式创建3个本地演示账号及样例记录")
    init.add_argument("--knowledge-dir", type=Path, help="可选：显式指定Markdown资料目录；默认自动发现或使用随附结构化快照")
    sub.add_parser("export", help="导出OpenAPI和SQLite/PostgreSQL初始DDL")
    args = parser.parse_args()
    if args.command == "init-db":
        from app.seed import seed_database
        if args.knowledge_dir is not None and not args.knowledge_dir.is_dir():
            parser.error(f"资料目录不存在：{args.knowledge_dir}")
        settings = Settings()
        settings.prepare()
        engine, factory = make_database(settings.database_url)
        Base.metadata.create_all(engine)
        with factory() as session:
            counts = seed_database(session, args.knowledge_dir, include_demo=args.demo)
            session.commit()
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        engine.dispose()
    else:
        docs = ROOT / "docs"
        docs.mkdir(exist_ok=True)
        app = create_app()
        (docs / "1021-openapi-v1.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
        sql_dir = BACKEND / "sql"
        sql_dir.mkdir(exist_ok=True)
        for name, dialect in (("postgresql", postgresql.dialect()), ("sqlite", sqlite.dialect())):
            statements = ["-- Generated from app/models.py by scripts/manage.py export.", "-- Initial schema only. Run on an empty database; future changes need migrations."]
            if name == "sqlite":
                statements.append("PRAGMA foreign_keys=ON;")
            statements.append("BEGIN;")
            for table in Base.metadata.sorted_tables:
                statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
                for index in sorted(table.indexes, key=lambda x: x.name):
                    statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
            statements.append("COMMIT;")
            sql_text = "\n".join(line.rstrip() for line in "\n\n".join(statements).splitlines()) + "\n"
            (sql_dir / f"001_init_{name}.sql").write_text(sql_text, encoding="utf-8")
        print("已导出 docs/1021-openapi-v1.json 和 backend/sql/001_init_*.sql")
        app.state.engine.dispose()


if __name__ == "__main__":
    main()
