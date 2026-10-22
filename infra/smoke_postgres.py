"""Real PostgreSQL restart/dump/restore drill in disposable, uniquely named resources."""
import argparse
from datetime import datetime, timezone
import gzip
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import uuid

from docx import Document
from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings
from backend.app.seed import seed_database
from gateway.main import create_app as create_gateway
from infra.gateway_backup import backup as backup_gateway, restore as restore_gateway
from infra.snapshot import encrypt, restore as restore_snapshot, seal, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wsl-distro', default='')
    options = parser.parse_args()
    docker = ['wsl.exe', '-d', options.wsl_distro, '-u', 'root', '--exec', 'docker'] if options.wsl_distro else ['docker']
    os.environ['WSL_UTF8'] = '1'
    name = 'zhigou-pg-drill-' + uuid.uuid4().hex[:12]
    root = Path('data/local', name).resolve()
    root.mkdir(parents=True)
    password = secrets.token_hex(32)
    secret = root / 'postgres.env'
    secret.write_text(f'POSTGRES_USER=zhigou\nPOSTGRES_DB=zhigou\nPOSTGRES_PASSWORD={password}\n', encoding='utf-8')
    checks = []
    holder = None
    report = {'verified_at': datetime.now(timezone.utc).isoformat(), 'database': 'real PostgreSQL 17', 'checks': checks}
    def run(*args, data=None):
        return subprocess.run(docker + list(args), input=data, capture_output=True, check=True, timeout=90).stdout
    def passed(label):
        checks.append(label)
        print('PASS ' + label, flush=True)
    def wait_database():
        for _ in range(40):
            # The image's init-only server accepts Unix sockets before its final restart, but no TCP.
            result = subprocess.run(docker + ['exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'zhigou', '-d', 'zhigou'], capture_output=True, timeout=10)
            if result.returncode == 0:
                return
            time.sleep(.5)
        raise RuntimeError('PostgreSQL did not become ready')
    def settings(database, uploads):
        return Settings(database_url=f'postgresql+psycopg://zhigou:{password}@127.0.0.1:{port}/{database}?connect_timeout=5',
                        secret_key='local-postgres-drill-key-at-least-32-characters', upload_dir=uploads)
    def login(client, username='demo_student'):
        response = client.post('/api/v1/auth/login', json={'username': username, 'password': 'Demo@2026!'})
        assert response.status_code == 200, response.text
        return {'Authorization': 'Bearer ' + response.json()['data']['access_token']}
    try:
        if options.wsl_distro:
            holder = subprocess.Popen(['wsl.exe', '-d', options.wsl_distro, '--exec', 'sleep', 'infinity'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        env_path = str(secret)
        if options.wsl_distro:
            env_path = subprocess.run(['wsl.exe', '-d', options.wsl_distro, '-u', 'root', '--exec', 'wslpath', '-a', '-u', str(secret)],
                                      capture_output=True, check=True, timeout=15).stdout.decode('utf-8').strip()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        run('run', '-d', '--name', name, '--label', 'zhigou.test=postgres', '--env-file', env_path,
            '-p', f'127.0.0.1:{port}:5432', '--mount', f'type=volume,source={name},target=/var/lib/postgresql/data', 'postgres:17-alpine')
        wait_database()
        assert run('port', name, '5432').decode().strip() == f'127.0.0.1:{port}'
        report['server_version'] = run('exec', name, 'psql', '-U', 'zhigou', '-Atc', 'SHOW server_version').decode().strip()
        source = create_app(settings('zhigou', root / 'uploads'))
        with TestClient(source) as client:
            with source.state.session_factory() as session:
                seed_database(session, include_demo=True)
                session.commit()
            headers, other = login(client), login(client, 'demo_new')
            assert client.get('/api/v1/knowledge-points').json()['data']['total'] == 60
            assert client.get('/api/v1/cases').json()['data']['total'] == 20
            passed('real database initialization, login and 60 knowledge points / 20 cases')
            document = Document()
            document.add_paragraph('PostgreSQL restore drill original document')
            memory = BytesIO()
            document.save(memory)
            content = memory.getvalue()
            response = client.post('/api/v1/documents', headers=headers, files={'file': ('restore.docx', content)})
            assert response.status_code == 201, response.text
            document_id = response.json()['data']['id']
            path = f'/api/v1/documents/{document_id}/content'
            assert client.get(path, headers=headers).content == content
            assert client.get(path, headers=other).status_code == 404
            passed('original file upload/download and cross-user access rejection')
            graph = {'title': 'Restore drill', 'diagram_type': 'class', 'data': {'nodes': [
                {'id': 'book', 'type': 'class', 'label': 'Book', 'position': {'x': 10, 'y': 20}}], 'edges': []}}
            response = client.post('/api/v1/diagrams', headers=headers, json=graph)
            assert response.status_code == 201, response.text
            diagram_id = response.json()['data']['diagram_id']
            graph_path = '/api/v1/diagrams/' + diagram_id
            assert client.put(graph_path, headers=headers, json={**graph, 'base_version': 1}).status_code == 200
            assert client.put(graph_path, headers=headers, json={**graph, 'base_version': 1}).status_code == 409
            passed('UML versions persist; stale update returns conflict')
            answer = {'exercise_id': 'Q-01', 'answer': 'A', 'client_event_id': name}
            first = client.post('/api/v1/learning/answers', headers=headers, json=answer)
            assert first.status_code in (200, 201), first.text
            repeat = client.post('/api/v1/learning/answers', headers=headers, json=answer)
            assert repeat.status_code == 201 and repeat.json()['data']['replayed'], repeat.text
            assert repeat.json()['data']['record']['id'] == first.json()['data']['record']['id']
            diagnosis = client.get('/api/v1/learning/diagnosis', headers=headers).json()['data']
            passed('answer idempotency and learning statistics on PostgreSQL')
            run('restart', name)
            wait_database()
            assert client.get(path, headers=login(client)).content == content
            assert client.get(graph_path, headers=headers).json()['data']['version'] == 2
            passed('database container restart retains business rows and original file')
        backup = root / 'snapshot'
        backup.mkdir()
        sql = run('exec', name, 'pg_dump', '-U', 'zhigou', '-d', 'zhigou', '--no-owner', '--no-acl')
        assert sql
        with gzip.open(backup / 'postgres.sql.gz', 'wb') as output:
            output.write(sql)
        shutil.copytree(root / 'uploads', backup / 'backend-uploads')
        gateway_source = root / 'gateway-source'
        with TestClient(create_gateway(gateway_source, start_worker=False)) as client:
            response = client.post('/api/uploads', files={'file': ('restore.docx', content)})
            assert response.status_code == 201, response.text
        backup_gateway(gateway_source, backup / 'gateway')
        (backup / 'model-budget').mkdir()
        for number in range(5):
            (backup / 'model-budget' / f'call-{number}.reserved').write_text('test reservation\n')
        seal(backup)
        verify(backup)
        passed('full PostgreSQL, gateway, business-file and model-budget snapshot checksums verified')
        key, certificate = root / 'test-private.pem', root / 'test-public.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=local-recovery-drill', '-keyout', str(key), '-out', str(certificate)],
                       capture_output=True, check=True, timeout=30)
        key.chmod(0o600)
        encrypted, recovered = root / 'snapshot.cms', root / 'recovered-snapshot'
        encrypt(backup, encrypted, certificate)
        restore_snapshot(encrypted, recovered, key)
        verify(recovered)
        assert len(list((recovered / 'model-budget').glob('call-*.reserved'))) == 8
        restored_gateway = root / 'restored-gateway'
        restore_gateway(recovered / 'gateway', restored_gateway)
        with TestClient(create_gateway(restored_gateway, start_worker=False)) as client:
            assert client.get('/health/live').status_code == 200
        passed('real AES-256-GCM encrypted export and cold recovery; gateway starts and model spending stays locked')
        run('exec', name, 'createdb', '-U', 'zhigou', 'zhigou_restore')
        run('exec', '-i', name, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'zhigou', '-d', 'zhigou_restore', data=gzip.decompress((recovered / 'postgres.sql.gz').read_bytes()))
        restored_uploads = root / 'restored-uploads'
        shutil.copytree(recovered / 'backend-uploads', restored_uploads)
        restored = create_app(settings('zhigou_restore', restored_uploads))
        with TestClient(restored) as client:
            headers = login(client)
            assert client.get(path, headers=headers).content == content
            assert client.get(path, headers=login(client, 'demo_new')).status_code == 404
            assert client.get(graph_path, headers=headers).json()['data']['version'] == 2
            assert client.get(graph_path + '?version=1', headers=headers).json()['data']['version'] == 1
            assert client.get('/api/v1/learning/diagnosis', headers=headers).json()['data'] == diagnosis
        passed('restored empty database plus new upload directory: login, permissions, file, both graph versions and learning statistics match')
        report['status'] = 'passed'
    finally:
        # Only resources with this invocation's random name are removed. No user database is touched.
        assert name.startswith('zhigou-pg-drill-') and len(name) == 28
        subprocess.run(docker + ['rm', '-f', name], capture_output=True, timeout=30)
        cleanup = subprocess.run(docker + ['volume', 'rm', name], capture_output=True, timeout=30)
        report['docker_cleanup'] = cleanup.returncode == 0
        (Path('data/local') / 'postgres-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if holder:
            holder.terminate()
            holder.wait(timeout=10)
    assert report['docker_cleanup'], 'Drill Docker volume was not cleaned up'
    print(f'{len(checks)} real PostgreSQL checks passed; disposable container and volume removed.')


if __name__ == '__main__':
    main()
