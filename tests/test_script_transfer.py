import base64
import os
import shlex
import subprocess
from unittest.mock import patch


from runpod_cli.cli import RunPodManager
from runpod_cli.utils import get_install, get_setup_root, get_setup_user, get_start, get_terminate


def embedded_scripts(args):
    """Read file contents out of the shell command sent to the pod API."""
    tokens = shlex.split(shlex.split(args)[2])
    return {tokens[i + 7].rstrip(';'): base64.b64decode(tokens[i + 2])
            for i, token in enumerate(tokens) if token == 'printf'}


def test_all_current_scripts_arrive_byte_for_byte(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    path = str(tmp_path / '.tmp_test')
    scripts = [get_setup_root(path, str(tmp_path)),
               get_setup_user(path, 'test@example.com', 'Test', 'export TEST="quotes: \' $ ` ☃"', 'alice'),
               get_install(path), get_start(path), get_terminate(path)]
    args = manager._build_docker_args(str(tmp_path), '.tmp_test', 1, scripts)
    # Execute the actual transfer commands without provisioning this local machine.
    bootstrap = shlex.split(args)[2].split('; bash ')[0]
    subprocess.run(['bash', '-c', bootstrap], check=True)
    for name, content in scripts:
        assert (tmp_path / '.tmp_test' / name).read_bytes() == content.encode('utf-8')
        subprocess.run(['bash', '-n', str(tmp_path / '.tmp_test' / name)], check=True)


def test_quoted_paths_and_runtime_sequence(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    directory = "space ' $dollar"
    scripts = [('start_pod.sh', 'printf start >> "$EVENTS"\nexit 1\n'),
               ('terminate_pod.sh', 'printf terminate >> "$EVENTS"\n'),
               ('empty.txt', '')]
    args = manager._build_docker_args(str(tmp_path), directory, 0, scripts)
    command = shlex.split(args)[2].replace('sleep 20', 'sleep 0')
    events = tmp_path / 'events'
    subprocess.run(['bash', '-c', command], env=dict(os.environ, EVENTS=str(events)), check=True)
    assert events.read_text() == 'startterminate'
    assert (tmp_path / directory / 'empty.txt').read_bytes() == b''


def test_write_failure_prevents_startup(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    (tmp_path / 'blocked').write_text('not a directory')
    marker = tmp_path / 'started'
    args = manager._build_docker_args(str(tmp_path), 'blocked', 0,
                                     [('start_pod.sh', f'touch {marker}')])
    result = subprocess.run(shlex.split(args), capture_output=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_init_needs_no_s3_credentials(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('RUNPOD_API_KEY=test\nRUNPOD_NETWORK_VOLUME_ID=volume\n')
    monkeypatch.delenv('RUNPOD_S3_ACCESS_KEY_ID', raising=False)
    monkeypatch.delenv('RUNPOD_S3_SECRET_KEY', raising=False)
    with patch('runpod_cli.cli.RunPodAPI') as api:
        api.return_value.get_network_volume.return_value = {'dataCenter': 'EU-RO-1'}
        manager = RunPodManager(env=str(env))
    assert manager._region == 'EU-RO-1'
    assert not hasattr(manager, '_s3')
