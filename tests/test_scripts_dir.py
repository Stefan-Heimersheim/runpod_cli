from pathlib import PurePosixPath
from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager
from test_script_transfer import embedded_scripts


def make_manager(pods):
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pods.return_value = pods
    manager._api.get_pub_key.return_value = ''
    manager._api.create_pod.side_effect = RuntimeError('stop before provisioning')
    manager._network_volume_id = 'vol'
    manager._region = 'EU'
    return manager


@pytest.mark.parametrize('mount', ['/network', '/custom-volume'])
def test_scripts_use_container_disk_and_logs_use_volume(monkeypatch, mount):
    monkeypatch.setattr(RunPodManager, 'reset', lambda self: None)
    manager = make_manager(pods=[])
    with pytest.raises(RuntimeError, match='stop before provisioning'):
        manager.create(gpu_type='CPU', name='test', volume_mount_path=mount)
    scripts = embedded_scripts(manager._api.create_pod.call_args.kwargs['docker_args'])
    assert len(scripts) == 5
    assert {str(PurePosixPath(path).parent) for path in scripts} == {'/opt/runpod_cli'}
    for content in scripts.values():
        assert f'exec > >(tee -a {mount}/runpod_cli_log.txt) 2>&1'.encode() in content
        assert b'.tmp_' not in content
    assert f'chown ubuntu:ubuntu {mount}/runpod_cli_log.txt'.encode() in scripts['/opt/runpod_cli/setup_root.sh']
    assert b'ln -s /opt/runpod_cli/terminate_pod.sh /usr/local/bin/terminate_pod' in scripts['/opt/runpod_cli/setup_root.sh']


@pytest.mark.parametrize('pods', [[], [{'id': 'existing'}]])
def test_create_without_s3_with_or_without_existing_pods(monkeypatch, pods):
    monkeypatch.setattr(RunPodManager, 'reset', lambda self: None)
    manager = make_manager(pods)
    with pytest.raises(RuntimeError, match='stop before provisioning'):
        manager.create(gpu_type='CPU', name='test')
    assert len(embedded_scripts(manager._api.create_pod.call_args.kwargs['docker_args'])) == 5
