"""Real Docker/JUnit checks through the authenticated gateway; rejects mock results."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import subprocess
import time

import httpx


CODE = 'public class Calculator { public int add(int a, int b) { return a + b; } }'
TEST = '''import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
public class CalculatorTest {
    @Test void add() { assertEquals(5, new Calculator().add(2, 3)); }
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wsl-distro', default='', help='WSL distribution hosting the Docker Engine')
    options = parser.parse_args()
    docker = ['wsl.exe', '--distribution', options.wsl_distro, '--user', 'root', '--exec', 'docker'] if options.wsl_distro else ['docker']
    def docker_output(*arguments):
        command = subprocess.run(docker + list(arguments), capture_output=True, encoding='utf-8', errors='replace', timeout=15)
        assert command.returncode == 0, command.stderr
        return command.stdout.strip()
    evidence = []
    isolation = None
    scenarios = [
        ('successful test', CODE, TEST, 'SUCCESS'),
        ('partial student coverage', CODE + '\nclass Extra { int unused() { return 10; } }', TEST, 'SUCCESS'),
        ('assertion failure', CODE, TEST.replace('assertEquals(5,', 'assertEquals(99,'), 'FAILED'),
        ('compile error', CODE.replace('return a + b;', 'return a + ;'), TEST, 'COMPILE_ERROR'),
        ('no JUnit tests', CODE, 'public class CalculatorTest {}', 'NO_TESTS'),
        ('infinite loop', CODE, TEST.replace('assertEquals(5, new Calculator().add(2, 3));', 'while (true) {}'), 'TIMEOUT'),
        ('network isolation', CODE, TEST.replace(
            'assertEquals(5, new Calculator().add(2, 3));',
            'assertThrows(java.io.IOException.class, () -> { try (var s = new java.net.Socket()) { '
            's.connect(new java.net.InetSocketAddress("1.1.1.1", 80), 500); } });'), 'SUCCESS'),
        ('JSON console escaping', CODE, TEST.replace(
            'assertEquals(5, new Calculator().add(2, 3));',
            r'System.out.println("C:\\sandbox\t\"quoted\""); assertEquals(5, new Calculator().add(2, 3));'), 'SUCCESS'),
    ]
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=20, trust_env=False) as client:
        login = client.post('/api/v1/auth/login', json={'username': 'demo_student', 'password': 'Demo@2026!'})
        assert login.status_code == 200, login.text
        headers = {'Authorization': 'Bearer ' + login.json()['data']['access_token']}
        for label, code, tests, expected in scenarios:
            started = time.monotonic()
            submitted = client.post('/api/code-tests', headers=headers, json={'code': code, 'tests': tests})
            assert submitted.status_code == 202, submitted.text
            task_id = submitted.json()['task_id']
            while time.monotonic() - started < 100:
                response = client.get('/api/tasks/' + task_id, headers=headers)
                assert response.status_code == 200, response.text
                task = response.json()
                if task['status'] in {'succeeded', 'failed'}:
                    break
                if label == 'infinite loop' and isolation is None:
                    running = docker_output('ps', '--filter', 'label=zhigou.sandbox=local', '-q').splitlines()
                    if running:
                        config = json.loads(docker_output('inspect', running[0]))[0]['HostConfig']
                        assert config['NetworkMode'] == 'none' and config['ReadonlyRootfs'], config
                        assert config['Memory'] == 256 * 1024 * 1024 and config['NanoCpus'] == 1000000000, config
                        assert config['PidsLimit'] == 64 and 'ALL' in config['CapDrop'], config
                        assert any(value.startswith('no-new-privileges') for value in config['SecurityOpt']), config
                        isolation = {key: config[key] for key in ('NetworkMode', 'ReadonlyRootfs', 'Memory', 'NanoCpus', 'PidsLimit', 'CapDrop', 'SecurityOpt')}
                time.sleep(.5)
            else:
                raise AssertionError(label + ' exceeded gateway budget')
            assert task['status'] == 'succeeded', (label, task)
            result = task['result']
            assert not result.get('mock') and result['status'] == expected, (label, result)
            if label == 'successful test':
                assert result['tests'] == {'success': 1, 'failed': 0}, result
                assert result['coverage']['line'] == 100.0, result
            if expected == 'FAILED':
                assert result['tests']['failed'] == 1, result
            if label == 'partial student coverage':
                assert 0 < result['coverage']['line'] < 100, result
            if expected == 'TIMEOUT':
                assert time.monotonic() - started < 30, 'Executor failed to enforce a short timeout'
            if label == 'JSON console escaping':
                assert 'C:\\sandbox\t"quoted"' in result['output'], result
            evidence.append({'scenario': label, 'status': result['status'], 'tests': result['tests'],
                             'coverage': result['coverage'], 'task_id': task_id,
                             'seconds': round(time.monotonic() - started, 2)})
            print(f'PASS {label}: {expected}', flush=True)
    assert isolation is not None, 'No live container security configuration was observed'
    assert not docker_output('ps', '-a', '--filter', 'label=zhigou.sandbox=local', '-q'), 'Task containers were not removed'
    assert not list(Path('agents/sandbox/tmp').glob('task-*')), 'Task directories were not removed'
    Path('data/local/sandbox-smoke.json').write_text(json.dumps({
        'verified_at': datetime.now(timezone.utc).isoformat(), 'executor': 'real Docker/JUnit',
        'checks': evidence, 'container_isolation': isolation, 'cleanup': 'passed'}, indent=2), encoding='utf-8')
    print(f'{len(evidence)} real sandbox scenarios, container isolation and cleanup checks passed.')


if __name__ == '__main__':
    main()
