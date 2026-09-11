from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager


def make_manager(pods):
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pods.return_value = pods
    manager._api.get_pub_key.return_value = ""
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._s3.list_objects_v2.return_value = {}
    manager._network_volume_id = "vol"
    manager._region = "EU"
    return manager


def uploaded_dirs(manager):
    return {call.kwargs["Key"].split("/")[0] for call in manager._s3.put_object.call_args_list}


def test_same_named_pods_get_distinct_script_dirs(monkeypatch):
    monkeypatch.setattr(RunPodManager, "reset", lambda self: None)
    dirs = set()
    for _ in range(2):
        manager = make_manager(pods=[{"id": "existing"}])
        with pytest.raises(RuntimeError, match="stop before provisioning"):
            manager.create(gpu_type="CPU", name="stefan-B200")
        (directory,) = uploaded_dirs(manager)
        assert directory.startswith(".tmp_stefan-B200_")
        dirs.add(directory)
    assert len(dirs) == 2


def test_leftover_script_dirs_are_cleared_when_no_pods_exist(monkeypatch):
    monkeypatch.setattr(RunPodManager, "reset", lambda self: None)
    manager = make_manager(pods=[])
    manager._s3.list_objects_v2.return_value = {
        "Contents": [{"Key": ".tmp_stefan-B200_deadbeef/log.txt"}, {"Key": ".tmp_stefan-B200_deadbeef/ssh_ed25519_host_key"}]
    }
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test")
    manager._s3.delete_objects.assert_called_once_with(
        Bucket="vol",
        Delete={"Objects": [{"Key": ".tmp_stefan-B200_deadbeef/log.txt"},
                            {"Key": ".tmp_stefan-B200_deadbeef/ssh_ed25519_host_key"}]},
    )


def test_no_cleanup_while_pods_are_running(monkeypatch):
    monkeypatch.setattr(RunPodManager, "reset", lambda self: None)
    manager = make_manager(pods=[{"id": "existing"}])
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test")
    manager._s3.delete_objects.assert_not_called()
