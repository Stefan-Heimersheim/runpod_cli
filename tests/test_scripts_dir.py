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


def test_same_named_pods_get_distinct_script_dirs(monkeypatch):
    monkeypatch.setattr(RunPodManager, 'reset', lambda self: None)
    dirs = set()
    for _ in range(2):
        manager = make_manager(pods=[{'id': 'existing'}])
        with pytest.raises(RuntimeError, match='stop before provisioning'):
            manager.create(gpu_type='CPU', name='stefan-B200')
        paths = embedded_scripts(manager._api.create_pod.call_args.kwargs['docker_args'])
        (directory,) = {PurePosixPath(path).parent.name for path in paths}
        assert directory.startswith('.tmp_stefan-B200_')
        dirs.add(directory)
    assert len(dirs) == 2


@pytest.mark.parametrize('pods', [[], [{'id': 'existing'}]])
def test_create_without_s3_with_or_without_existing_pods(monkeypatch, pods):
    monkeypatch.setattr(RunPodManager, 'reset', lambda self: None)
    manager = make_manager(pods)
    with pytest.raises(RuntimeError, match='stop before provisioning'):
        manager.create(gpu_type='CPU', name='test')
    assert len(embedded_scripts(manager._api.create_pod.call_args.kwargs['docker_args'])) == 5
