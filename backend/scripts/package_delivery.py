"""Build a source-only handoff ZIP, excluding local accounts, uploads and secrets."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "delivery" / "1021-知构后端1交付-v1.zip"
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", "storage", "test-results"}
EXCLUDED_NAMES = {".env", ".dev-jwt-secret"}


def main():
    paths = [ROOT / name for name in ("README.md", "run-local.ps1", "pytest.ini", ".gitignore", ".dockerignore")]
    for directory in ("backend", "docs", "knowledge-base"):
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if EXCLUDED_DIRS.intersection(relative.parts) or path.name in EXCLUDED_NAMES:
                continue
            if path.suffix in {".pyc", ".db", ".db-shm", ".db-wal"}:
                continue
            paths.append(path)
    paths = sorted(set(paths), key=lambda p: p.relative_to(ROOT).as_posix())
    checksums = []
    DEST.parent.mkdir(exist_ok=True)
    with ZipFile(DEST, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            archive.writestr(relative, content)
            checksums.append(f"{hashlib.sha256(content).hexdigest()}  {relative}")
        archive.writestr("MANIFEST.sha256", "\n".join(checksums) + "\n")
    with ZipFile(DEST) as archive:
        assert archive.testzip() is None
        assert not any("/.env" == "/" + name or "/storage/" in "/" + name or name.endswith((".db", ".dev-jwt-secret", ".pyc")) for name in archive.namelist())
    print(json.dumps({"file": str(DEST), "source_files": len(paths), "bytes": DEST.stat().st_size, "sha256": hashlib.sha256(DEST.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
