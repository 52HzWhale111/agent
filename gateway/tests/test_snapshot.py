"""Offline full-backup recovery, including real OpenSSL authentication failures."""
import asyncio
from datetime import datetime, timedelta, timezone
import gzip
from io import BytesIO
import json
import shutil
import subprocess
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest

from gateway.main import create_app
from infra.cloud_knowledge import TrialTransport
from infra.gateway_backup import backup, restore as restore_gateway
from infra.snapshot import encrypt, health, restore, seal, verify


@pytest.fixture
def snapshot(tmp_path):
    source, target = tmp_path / "source", tmp_path / "snapshot"
    with TestClient(create_app(source, start_worker=False)) as client:
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("Main.java", "class Main {}")
        uploaded = client.post("/api/uploads", files={"file": ("project.zip", data.getvalue())}).json()
        assert client.post("/api/code-tests", json={"file_id": uploaded["file_id"]}).status_code == 202
    target.mkdir()
    with gzip.open(target / "postgres.sql.gz", "wb") as output:
        output.write(b"-- Synthetic dump; real PostgreSQL is tested in smoke_postgres\nSELECT 1;")
    backup(source, target / "gateway")
    (target / "backend-uploads").mkdir()
    (target / "backend-uploads" / "original.docx").write_bytes(b"original business file")
    (target / "model-budget").mkdir()
    for number in range(5):
        (target / "model-budget" / f"call-{number}.reserved").write_text("reserved\n")
    seal(target)
    return target


@pytest.fixture
def recipient(tmp_path):
    key, certificate = tmp_path / "private.pem", tmp_path / "public.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=local-backup-test", "-keyout", str(key), "-out", str(certificate)],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return key, certificate


@pytest.mark.parametrize("change", ["dump", "business", "missing", "extra", "budget"])
def test_full_snapshot_rejects_changed_or_missing_components(snapshot, change):
    if change == "dump":
        path = snapshot / "postgres.sql.gz"
        path.write_bytes(path.read_bytes()[:-4])
    elif change == "business":
        (snapshot / "backend-uploads" / "original.docx").write_bytes(b"changed")
    elif change == "missing":
        (snapshot / "gateway" / "gateway.sqlite3").unlink()
    elif change == "extra":
        (snapshot / ".env").write_text("not allowed")
    else:
        (snapshot / "model-budget" / "call-0.reserved").unlink()
    with pytest.raises(ValueError):
        verify(snapshot)


def test_seal_reads_dump_crc_and_refuses_symlinks(snapshot, tmp_path):
    (snapshot / "snapshot.json").unlink()
    path = snapshot / "postgres.sql.gz"
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises((EOFError, OSError)):
        seal(snapshot)
    (snapshot / "snapshot.json").unlink()
    try:
        (snapshot / "backend-uploads" / "link").symlink_to(path)
    except OSError:
        return  # Windows without symlink privilege still exercises truncated-dump rejection.
    with pytest.raises(ValueError, match="Symlinks"):
        seal(snapshot)


def test_encrypt_restore_files_tasks_and_fail_closed_budget(snapshot, recipient, tmp_path):
    key, certificate = recipient
    encrypted, restored = tmp_path / "backup.cms", tmp_path / "restored"
    encrypt(snapshot, encrypted, certificate)
    original = verify(snapshot)
    assert b"original business file" not in encrypted.read_bytes()
    assert restore(encrypted, restored, key)["model_calls_locked"]
    assert len(list((snapshot / "model-budget").iterdir())) == 5  # Source is never modified.
    assert len(list((restored / "model-budget").iterdir())) == 8
    assert verify(restored)["budget_guarded"]
    assert verify(restored)["created_at"] == original["created_at"]  # Old data must not look newly backed up.
    assert (restored / "backend-uploads" / "original.docx").read_bytes() == b"original business file"
    assert (restored / "postgres.sql.gz").read_bytes() == (snapshot / "postgres.sql.gz").read_bytes()
    assert restore_gateway(restored / "gateway", tmp_path / "live-gateway")["rebased_tasks"] == 1
    assert verify(snapshot) == original

    async def no_spending_after_cold_recovery():
        def unexpected(request):
            pytest.fail("Recovered trial must not call a model")
        transport = TrialTransport(restored / "model-budget", httpx.MockTransport(unexpected))
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions", json={
            "model": "deepseek-flash", "max_tokens": 10, "messages": [{"role": "user", "content": "test"}]})
        assert (await transport.handle_async_request(request)).status_code == 402
        await transport.aclose()
    asyncio.run(no_spending_after_cold_recovery())
    with pytest.raises(FileExistsError):
        encrypt(snapshot, encrypted, certificate)
    with pytest.raises(FileExistsError):
        restore(encrypted, snapshot, key)


def test_wrong_key_and_tampering_never_publish_plaintext(snapshot, recipient, tmp_path):
    key, certificate = recipient
    encrypted = tmp_path / "backup.cms"
    encrypt(snapshot, encrypted, certificate)
    other_key = tmp_path / "other.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(other_key)], check=True, capture_output=True)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(subprocess.CalledProcessError):
        restore(encrypted, destination, other_key)
    damaged = bytearray(encrypted.read_bytes())
    damaged[-1] ^= 1  # GCM authentication tag, not an incidental ZIP checksum.
    encrypted.write_bytes(damaged)
    with pytest.raises(subprocess.CalledProcessError):
        restore(encrypted, destination, key)
    assert not destination.exists()
    assert not list(tmp_path.glob(".zhigou-*"))


@pytest.mark.parametrize("name", ["../outside", "gateway/../../outside", "C:/outside", "gateway\\outside"])
def test_even_encrypted_archive_cannot_escape_restore_directory(recipient, tmp_path, name):
    key, certificate = recipient
    archive, encrypted = tmp_path / "unsafe.zip", tmp_path / "unsafe.cms"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, "untrusted")
    if "\\" in name:
        # ZipInfo normalizes Windows separators when writing; exercise the raw wire name too.
        archive.write_bytes(archive.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
    subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm", "-in", str(archive),
                    "-outform", "DER", "-out", str(encrypted), "-recip", str(certificate)],
                   check=True, capture_output=True)
    with pytest.raises(ValueError, match="Unsafe"):
        restore(encrypted, tmp_path / "restored", key)
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "restored").exists()


def test_regular_deployment_with_no_model_guard_roundtrips(snapshot, recipient, tmp_path):
    key, certificate = recipient
    (snapshot / "snapshot.json").unlink()
    shutil.rmtree(snapshot / "model-budget")
    (snapshot / "backend-uploads" / "original.docx").unlink()  # Also preserve an empty upload directory.
    seal(snapshot)
    encrypted, restored = tmp_path / "regular.cms", tmp_path / "restored"
    encrypt(snapshot, encrypted, certificate)
    assert not restore(encrypted, restored, key)["model_calls_locked"]
    assert verify(restored) == verify(snapshot)
    assert not (restored / "model-budget").exists()


def test_health_rejects_stale_backups_and_low_disk_space(snapshot, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=6 * 1024 ** 3))
    assert health(snapshot)["free_gib"] == 6
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=4 * 1024 ** 3))
    with pytest.raises(ValueError, match="disk space"):
        health(snapshot)
    manifest = json.loads((snapshot / "snapshot.json").read_text())
    manifest["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    (snapshot / "snapshot.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="stale"):
        health(snapshot)


def test_encrypted_publisher_reuses_verified_snapshot_without_reencrypting(snapshot, recipient, tmp_path, monkeypatch):
    from infra.backup_export import publish
    from infra.gateway_backup import fingerprint
    from types import SimpleNamespace
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: SimpleNamespace(free=20*1024**3))
    key, certificate = recipient
    source = snapshot.rename(tmp_path / 'snapshot-20261022T053336Z-171088')
    exports = tmp_path / 'exports'
    first = publish(tmp_path, exports, certificate)
    cipher = exports / (source.name + '.cms')
    before = fingerprint(cipher)
    catalog = json.loads((exports / 'index.json').read_text())
    assert catalog['backups'] == [{'filename': cipher.name, **before}]
    assert publish(tmp_path, exports, certificate) == first
    assert fingerprint(cipher) == before
    assert set(p.name for p in exports.iterdir()) == {'index.json', cipher.name}
    restored = tmp_path / 'publisher-restored'
    assert restore(cipher, restored, key)['model_calls_locked']
