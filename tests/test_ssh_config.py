import shutil
import subprocess
from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager


@pytest.fixture
def manager():
    return RunPodManager.__new__(RunPodManager)


def test_each_pod_gets_its_own_numbered_file_and_runpod_points_to_the_newest(manager, tmp_path):
    path = tmp_path / "config.runpod_cli"
    manager._write_ssh_config("192.0.2.1", 2201, False, str(path))
    manager._write_ssh_config("192.0.2.2", 2202, True, str(path))
    manager._write_ssh_config("192.0.2.3", 2203, False, str(path))
    assert path.read_text() == f"Include {path}.default\nInclude {path}.1\nInclude {path}.2\nInclude {path}.3\n"
    assert "Host runpod\n" in (tmp_path / "config.runpod_cli.default").read_text()
    assert "Host runpod1" in (tmp_path / "config.runpod_cli.1").read_text()
    if shutil.which("ssh"):
        for host, ip, port in [("runpod", "192.0.2.3", 2203), ("runpod1", "192.0.2.1", 2201),
                               ("runpod2", "192.0.2.2", 2202), ("runpod3", "192.0.2.3", 2203)]:
            result = subprocess.run(["ssh", "-G", "-F", str(path), host], capture_output=True, text=True, check=True)
            assert f"hostname {ip}\n" in result.stdout
            assert f"port {port}\n" in result.stdout


def test_legacy_file_is_replaced_by_the_first_new_pod(manager, tmp_path):
    path = tmp_path / "config.runpod_cli"
    path.write_text("Host runpod\n  HostName 192.0.2.1\n  User user\n  Port 2201\n")
    manager._write_ssh_config("192.0.2.2", 2202, False, str(path))
    assert path.read_text() == f"Include {path}.default\nInclude {path}.1\n"


def test_reset_removes_all_files_and_restarts_numbering(manager, tmp_path):
    path = tmp_path / "config.runpod_cli"
    manager._write_ssh_config("192.0.2.1", 2201, False, str(path))
    manager._write_ssh_config("192.0.2.2", 2202, False, str(path))
    manager.reset(str(path))
    assert not list(tmp_path.iterdir())
    manager._write_ssh_config("192.0.2.3", 2203, False, str(path))
    assert path.read_text() == f"Include {path}.default\nInclude {path}.1\n"


@pytest.mark.parametrize("pods,expect_reset", [([], True), ([{"id": "existing"}], False)])
def test_create_resets_only_when_no_pods_exist(manager, monkeypatch, pods, expect_reset):
    manager._api = Mock()
    manager._api.get_pods.return_value = pods
    reset_calls = []
    monkeypatch.setattr(RunPodManager, "reset", lambda self: reset_calls.append(True))
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU"
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test")
    assert bool(reset_calls) == expect_reset
