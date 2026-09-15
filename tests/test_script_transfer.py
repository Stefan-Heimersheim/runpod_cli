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
    scripts = [get_setup_root(str(tmp_path)),
               get_setup_user('test@example.com', 'Test', 'export TEST="quotes: \' $ ` ☃"', 'alice'),
               get_install(), get_start(), get_terminate()]
    args = manager._build_docker_args(1, scripts)
    # Execute the actual transfer commands without provisioning this local machine.
    bootstrap = shlex.split(args)[2].split('; bash ')[0].replace("/opt/runpod_cli", shlex.quote(str(tmp_path / "scripts")))
    subprocess.run(['bash', '-c', bootstrap], check=True)
    for name, content in scripts:
        assert (tmp_path / 'scripts' / name).read_bytes() == content.encode('utf-8')
        subprocess.run(['bash', '-n', str(tmp_path / 'scripts' / name)], check=True)


def test_empty_file_and_runtime_sequence(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    directory = "scripts"
    scripts = [('start_pod.sh', 'printf start >> "$EVENTS"\nexit 1\n'),
               ('terminate_pod.sh', 'printf terminate >> "$EVENTS"\n'),
               ('empty.txt', '')]
    args = manager._build_docker_args(0, scripts)
    command = shlex.split(args)[2].replace('/opt/runpod_cli', shlex.quote(str(tmp_path / 'scripts'))).replace('sleep 20', 'sleep 0')
    events = tmp_path / 'events'
    subprocess.run(['bash', '-c', command], env=dict(os.environ, EVENTS=str(events)), check=True)
    assert events.read_text() == 'startterminate'
    assert (tmp_path / directory / 'empty.txt').read_bytes() == b''


def test_write_failure_continues_to_setup_and_termination(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    directory = tmp_path / 'scripts'
    directory.mkdir()
    (directory / 'blocked').mkdir()
    scripts = [('blocked', 'cannot overwrite a directory'),
               ('start_pod.sh', 'printf start >> "$EVENTS"\nexit 1\n'),
               ('terminate_pod.sh', 'printf terminate >> "$EVENTS"\n')]
    args = manager._build_docker_args(0, scripts)
    command = shlex.split(args)[2].replace('/opt/runpod_cli', shlex.quote(str(tmp_path / 'scripts'))).replace('sleep 20', 'sleep 0')
    events = tmp_path / 'events'
    result = subprocess.run(['bash', '-c', command],
                            env=dict(os.environ, EVENTS=str(events)), capture_output=True)
    assert result.returncode == 0
    assert events.read_text() == 'startterminate'


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
