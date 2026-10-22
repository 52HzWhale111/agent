"""Verify a full backup, encrypt it for an offline recipient, restore into a new directory."""
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from infra.gateway_backup import fingerprint, verify as verify_gateway


def safe_name(name):
    parts = name.split("/")
    return (not any(p in {"", ".", ".."} or p != p.rstrip(" .") for p in parts)
            and not re.search(r"[\x00-\x1f\\:]", name)
            and (name in {"postgres.sql.gz", "backend-uploads.sha256", "snapshot.json"}
                 or parts[0] in {"gateway", "backend-uploads"} and len(parts) > 1
                 or re.fullmatch(r"model-budget/call-[0-7]\.reserved", name) is not None))


def files(source):
    if source.is_symlink():
        raise ValueError("Backup directory must not be a symlink")
    result = {}
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks are not valid backup entries")
        if path.is_file():
            name = path.relative_to(source).as_posix()
            if not safe_name(name):
                raise ValueError("Unexpected backup entry")
            if name != "snapshot.json":
                result[name] = fingerprint(path)
        elif not path.is_dir():
            raise ValueError("Backup entries must be regular files or directories")
    return result


def verify(source):
    source = Path(source)
    manifest = json.loads((source / "snapshot.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or type(manifest.get("budget_guarded")) is not bool:
        raise ValueError("Unsupported snapshot manifest")
    if datetime.fromisoformat(manifest["created_at"]).tzinfo is None:
        raise ValueError("Snapshot time must include its timezone")
    if files(source) != manifest.get("files"):
        raise ValueError("Snapshot checksum or file list mismatch")
    # Read the complete stream: gzip header checks alone miss truncation/CRC failures.
    with gzip.open(source / "postgres.sql.gz", "rb") as sql:
        if not sql.read(1):
            raise ValueError("Empty PostgreSQL dump")
        while sql.read(1024 * 1024):
            pass
    if not (source / "backend-uploads").is_dir():
        raise ValueError("Missing business upload directory")
    if (source / "model-budget").is_dir() != manifest["budget_guarded"]:
        raise ValueError("Missing or unexpected model budget directory")
    verify_gateway(source / "gateway")
    return manifest


def seal(source):
    source = Path(source)
    manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "budget_guarded": (source / "model-budget").is_dir(), "files": files(source)}
    with (source / "snapshot.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
    return verify(source)


def health(source, max_age_hours=36, min_free_gib=5):
    manifest = verify(source)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(manifest["created_at"])).total_seconds() / 3600
    free = shutil.disk_usage(source).free / 1024 ** 3
    if not 0 <= age <= max_age_hours:
        raise ValueError("Backup is stale or its timestamp is in the future")
    if free < min_free_gib:
        raise ValueError("Backup disk space is below the configured threshold")
    return {"age_hours": round(age, 2), "free_gib": round(free, 2), "budget_guarded": manifest["budget_guarded"]}


def encrypt(source, destination, certificate):
    source, destination = Path(source), Path(destination)
    manifest = verify(source)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Encrypted export must be outside the snapshot")
    with tempfile.TemporaryDirectory(prefix=".zhigou-export-", dir=destination.parent) as temp:
        archive, encrypted = Path(temp) / "snapshot.zip", Path(temp) / "snapshot.cms"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in [*manifest["files"], "snapshot.json"]:
                bundle.write(source / name, name)
        # Recheck after reading so concurrent edits cannot create a misleading export.
        verify(source)
        subprocess.run([os.getenv("OPENSSL", "openssl"), "cms", "-encrypt", "-binary", "-aes-256-gcm",
                        "-in", str(archive), "-outform", "DER", "-out", str(encrypted),
                        "-recip", str(certificate), "-keyopt", "rsa_padding_mode:oaep"],
                       check=True, capture_output=True)
        encrypted.chmod(0o600)
        # Same-filesystem hard link publishes a complete file without overwriting an existing export.
        os.link(encrypted, destination)
    return {"encrypted": str(destination), "files": len(manifest["files"])}


def restore(source, destination, key):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix=".zhigou-restore-", dir=destination.parent) as temp:
        archive, staged = Path(temp) / "snapshot.zip", Path(temp) / "snapshot"
        # OpenSSL may write plaintext before checking the GCM tag. Never parse it until exit status 0.
        subprocess.run([os.getenv("OPENSSL", "openssl"), "cms", "-decrypt", "-binary", "-inform", "DER",
                        "-in", str(source), "-inkey", str(key), "-out", str(archive)],
                       check=True, capture_output=True)
        staged.mkdir(mode=0o700)
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            # ZipInfo.filename normalizes backslashes on Windows; inspect the original wire name.
            names = [entry.orig_filename for entry in members]
            if len(names) != len(set(names)) or any(not safe_name(name) for name in names):
                raise ValueError("Unsafe or duplicate archive entry")
            required = sum(entry.file_size for entry in members)
            if required + 256 * 1024 * 1024 > shutil.disk_usage(temp).free:
                raise ValueError("Insufficient space for restore")
            for entry in members:
                path = staged / entry.filename
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with bundle.open(entry) as content, path.open("xb") as output:
                    shutil.copyfileobj(content, output)
                path.chmod(0o600)
        manifest = json.loads((staged / "snapshot.json").read_text(encoding="utf-8"))
        # Empty directories are deliberately not ZIP members.
        (staged / "backend-uploads").mkdir(exist_ok=True)
        if manifest.get("budget_guarded"):
            (staged / "model-budget").mkdir(exist_ok=True)
        verify(staged)
        # An old snapshot cannot prove how many calls happened AFTER it was taken.
        # Cold recovery therefore fails closed; no automatic restoration of spending permission.
        if manifest["budget_guarded"]:
            for number in range(8):
                with (staged / "model-budget" / f"call-{number}.reserved").open("a") as ticket:
                    ticket.write("recovery lock; new spending requires explicit authorization\n")
            manifest["files"] = files(staged)
            manifest["recovered_at"] = datetime.now(timezone.utc).isoformat()
            (staged / "snapshot.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            verify(staged)
        # Refuse even an empty existing directory; this tool never switches live volumes.
        destination.mkdir(mode=0o700)
        for path in staged.iterdir():
            shutil.move(str(path), destination / path.name)
    return {"restored": str(destination), "model_calls_locked": manifest["budget_guarded"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["seal", "verify", "health", "encrypt", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--max-age-hours", type=float, default=36)
    parser.add_argument("--min-free-gib", type=float, default=5)
    args = parser.parse_args()
    if args.operation in {"seal", "verify"}:
        manifest = (seal if args.operation == "seal" else verify)(args.source)
        result = {"files": len(manifest["files"]), "budget_guarded": manifest["budget_guarded"]}
    elif args.operation == "health":
        result = health(args.source, args.max_age_hours, args.min_free_gib)
    elif args.operation == "encrypt" and args.destination and args.certificate:
        result = encrypt(args.source, args.destination, args.certificate)
    elif args.operation == "restore" and args.destination and args.key:
        result = restore(args.source, args.destination, args.key)
    else:
        parser.error("encrypt requires destination/--certificate; restore requires destination/--key")
    print(json.dumps({"operation": args.operation, "result": result}))


if __name__ == "__main__":
    main()
