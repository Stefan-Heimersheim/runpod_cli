import shutil
import subprocess

import pytest

from runpod_cli.cli import RunPodManager


@pytest.fixture
def manager():
    return RunPodManager.__new__(RunPodManager)


def test_each_pod_gets_a_numbered_alias_and_runpod_points_to_the_newest(manager, tmp_path):
    path = tmp_path / "config.runpod_cli"
    manager._write_ssh_config("192.0.2.1", 2201, False, str(path))
    manager._write_ssh_config("192.0.2.2", 2202, True, str(path))
    manager._write_ssh_config("192.0.2.3", 2203, False, str(path))
    text = path.read_text()
    assert "Host runpod runpod3" in text
    assert "Host runpod1" in text and "Host runpod2" in text
    assert text.count("Host runpod ") == 1
    if shutil.which("ssh"):
        for host, ip, port in [("runpod", "192.0.2.3", 2203), ("runpod1", "192.0.2.1", 2201),
                               ("runpod2", "192.0.2.2", 2202), ("runpod3", "192.0.2.3", 2203)]:
            result = subprocess.run(["ssh", "-G", "-F", str(path), host], capture_output=True, text=True, check=True)
            assert f"hostname {ip}\n" in result.stdout
            assert f"port {port}\n" in result.stdout


def test_legacy_single_entry_file_is_renumbered(manager, tmp_path):
    path = tmp_path / "config.runpod_cli"
    path.write_text("Host runpod\n  HostName 192.0.2.1\n  User user\n  Port 2201\n")
    manager._write_ssh_config("192.0.2.2", 2202, False, str(path))
    text = path.read_text()
    assert "Host runpod runpod1" in text
    assert "Host runpod0" in text
    assert "HostName 192.0.2.1" in text
