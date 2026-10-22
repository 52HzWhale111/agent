"""SQLite online backup plus its registered uploads; restore only into a new directory."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


def connect_readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def registered_files(database):
    with closing(connect_readonly(database)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        rows = conn.execute("SELECT id, suffix, size FROM files").fetchall()
    files = {}
    for file_id, suffix, size in rows:
        if not re.fullmatch(r"[0-9a-f]{32}", file_id) or suffix not in {".doc", ".docx", ".pdf", ".zip"}:
            raise ValueError("Invalid upload record")
        files[f"uploads/{file_id}{suffix}"] = size
    return files


def fingerprint(path):
    if path.is_symlink():
        raise ValueError("Symlinks are not valid backup files")
    digest = hashlib.sha256()
    with path.open("rb") as content:
        for chunk in iter(lambda: content.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / "gateway.sqlite3").is_file():
        raise FileNotFoundError("Source gateway.sqlite3 does not exist")
    destination.mkdir(parents=True, exist_ok=False)
    with closing(connect_readonly(source / "gateway.sqlite3")) as current:
        with closing(sqlite3.connect(destination / "gateway.sqlite3")) as snapshot:
            current.backup(snapshot)
    uploads = registered_files(destination / "gateway.sqlite3")
    (destination / "uploads").mkdir()
    for name, size in uploads.items():
        current = source / name
        if current.is_symlink() or current.resolve().parent != (source / "uploads").resolve():
            raise ValueError("Upload must be a regular file inside uploads")
        if current.stat().st_size != size:
            raise ValueError(f"Upload size mismatch: {name}")
        shutil.copyfile(current, destination / name)
    manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "files": {name: fingerprint(destination / name) for name in ["gateway.sqlite3", *uploads]}}
    # A partial snapshot has no manifest and cannot pass verification or be restored.
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    verify(destination)
    return manifest


def verify(source):
    source = Path(source).resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or "gateway.sqlite3" not in manifest.get("files", {}):
        raise ValueError("Unsupported or incomplete backup")
    for name, expected in manifest["files"].items():
        if not re.fullmatch(r"gateway\.sqlite3|uploads/[0-9a-f]{32}\.(doc|docx|pdf|zip)", name):
            raise ValueError("Unsafe backup path")
        path = source / name
        if path.resolve().parent != (source if name == "gateway.sqlite3" else source / "uploads").resolve():
            raise ValueError("Unsafe backup path")
        if fingerprint(path) != expected:
            raise ValueError(f"Backup checksum mismatch: {name}")
    uploads = registered_files(source / "gateway.sqlite3")
    if set(manifest["files"]) != {"gateway.sqlite3", *uploads}:
        raise ValueError("Backup does not match registered uploads")
    if any(manifest["files"][name]["size"] != size for name, size in uploads.items()):
        raise ValueError("Upload index size mismatch")
    return manifest


def restore(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = verify(source)
    # Rebase queued ZIP task paths when restoring to another machine/directory.
    changes = []
    with closing(connect_readonly(source / "gateway.sqlite3")) as conn:
        for task_id, value in conn.execute("SELECT id, payload FROM tasks WHERE status='queued' AND kind='code'"):
            payload = json.loads(value)
            if "archive_path" in payload:
                filename = payload["archive_path"].replace("\\", "/").rsplit("/", 1)[-1]
                if "uploads/" + filename not in manifest["files"]:
                    raise ValueError("Queued task refers to an unregistered archive")
                payload["archive_path"] = str(destination / "uploads" / filename)
                changes.append((json.dumps(payload, ensure_ascii=False), task_id))
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "uploads").mkdir()
    for name in manifest["files"]:
        shutil.copyfile(source / name, destination / name)
    with closing(sqlite3.connect(destination / "gateway.sqlite3")) as conn:
        with conn:
            conn.executemany("UPDATE tasks SET payload=? WHERE id=?", changes)
    return {"restored_uploads": len(manifest["files"]) - 1, "rebased_tasks": len(changes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "verify", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    if args.operation == "verify":
        result = verify(args.source)
    else:
        if args.destination is None:
            parser.error("backup/restore require a destination")
        result = (backup if args.operation == "backup" else restore)(args.source, args.destination)
    print(json.dumps({"operation": args.operation, "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
