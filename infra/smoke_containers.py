"""Verify packaged Agent APIs; model absence is an expected 503, never an AI success."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

import httpx

from gateway.smoke_sandbox import CODE, TEST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wsl-distro', default='')
    options = parser.parse_args()
    docker = ['wsl.exe', '-d', options.wsl_distro, '-u', 'root', '--exec', 'docker'] if options.wsl_distro else ['docker']
    os.environ['WSL_UTF8'] = '1'
    prefix = 'zhigou-api-drill-' + uuid.uuid4().hex[:12]
    root = Path('data/local', prefix).resolve()
    root.mkdir(parents=True)
    names = []
    holder = None
    report = {'verified_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    def run(*args):
        return subprocess.run(docker + list(args), capture_output=True, check=True, timeout=30).stdout.decode('utf-8').strip()
    def launch(kind, image, container_port, arguments):
        name = prefix + '-' + kind
        names.append(name)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        run('run', '-d', '--name', name, '--label', 'zhigou.test=agent-api',
            '-p', f'127.0.0.1:{port}:{container_port}', *arguments, image)
        return f'http://127.0.0.1:{port}'
    try:
        if options.wsl_distro:
            holder = subprocess.Popen(['wsl.exe', '-d', options.wsl_distro, '--exec', 'sleep', 'infinity'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        work = str(root)
        if options.wsl_distro:
            work = subprocess.check_output(['wsl.exe', '-d', options.wsl_distro, '-u', 'root', '--exec', 'wslpath', '-a', '-u', work]).decode('utf-8').strip()
        # cap-drop ALL removes DAC override: align only this new empty bind directory with API UID 0.
        run('run', '--rm', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--security-opt', 'no-new-privileges',
            '-v', work + ':/work', '--entrypoint', 'chown', 'zhigou-sandbox-api:local', '0:0', '/work')
        sandbox = launch('sandbox', 'zhigou-sandbox-api:local', 8200, [
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:size=64m',
            '-e', 'SANDBOX_TMP_DIR=' + work, '-v', work + ':' + work,
            '-v', '/var/run/docker.sock:/var/run/docker.sock:ro'])
        with httpx.Client(trust_env=False, timeout=65) as client:
            for _ in range(40):
                try:
                    if client.options(sandbox + '/sandbox/run-tests', timeout=1).status_code == 204:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.25)
            for expected, test in [('SUCCESS', TEST), ('TIMEOUT', TEST.replace('assertEquals(5, new Calculator().add(2, 3));', 'while (true) {}'))]:
                print('START packaged sandbox API: ' + expected, flush=True)
                response = client.post(sandbox + '/sandbox/run-tests', content=CODE + '\n---TEST---\n' + test,
                                       headers={'Content-Type': 'text/plain; charset=utf-8'})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['status'] == expected, result
                if expected == 'SUCCESS':
                    assert result['tests']['success'] == 1 and result['coverage']['line'] == 100
                report['checks'].append('packaged sandbox API: ' + expected)
                print('PASS packaged sandbox API: ' + expected, flush=True)
            assert not list(root.glob('task-*')), 'Task files were not cleaned up'
            knowledge = launch('knowledge', 'zhigou-knowledge:local', 8100, [])
            for _ in range(40):
                try:
                    if client.get(knowledge + '/openapi.json', timeout=1).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.25)
            response = client.get(knowledge + '/health')
            assert response.status_code == 503 and response.json()['knowledge_points'] == 60
            assert response.json()['supplemental_points'] == 2
            response = client.post(knowledge + '/qa', json={'question': '什么是基线'})
            assert response.status_code == 503 and response.json()['detail']['code'] == 'MODEL_NOT_CONFIGURED'
            response = client.post(knowledge + '/qa', json={'question': '概要设计和详细设计有什么区别'})
            assert response.status_code == 503 and response.json()['detail']['code'] == 'MODEL_NOT_CONFIGURED'
            report['checks'].append('packaged knowledge API loads 60 original + 2 supplemental points and templates; absent credentials rejected')
            print('PASS packaged knowledge API: no placeholder success without credentials', flush=True)
            report['status'] = 'passed'
    finally:
        report['api_logs'] = {}
        for name in names:
            assert name.startswith(prefix + '-')
            logs = subprocess.run(docker + ['logs', '--tail', '30', name], capture_output=True, timeout=15)
            report['api_logs'][name] = (logs.stdout + logs.stderr).decode('utf-8', errors='replace')[-6000:]
            subprocess.run(docker + ['rm', '-f', name], capture_output=True, timeout=30)
        report['containers_removed'] = all(not run('ps', '-a', '--filter', 'name=^/' + name + '$', '-q') for name in names)
        Path('data/local/container-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if holder:
            holder.terminate()
            holder.wait(timeout=10)
    assert report['containers_removed']
    print('Packaged Agent API checks passed and test containers removed.')


if __name__ == '__main__':
    main()
