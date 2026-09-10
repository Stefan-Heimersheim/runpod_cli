import os
import shutil
import subprocess

import pytest

from runpod_cli.cli import RunPodManager


@pytest.fixture
def manager():
    return RunPodManager.__new__(RunPodManager)


def test_multiple_hosts_and_updating_one_preserves_others(manager, tmp_path):
    path = tmp_path / ".ssh" / "config.runpod_cli"
    manager._write_ssh_config("192.0.2.1", 2201, False, str(path), "training")
    manager._write_ssh_config("192.0.2.2", 2202, True, str(path), "analysis")
    manager._write_ssh_config("192.0.2.3", 2203, False, str(path), "training")
    text = path.read_text()
    assert text.count("Host training\n") == 1
    assert "HostName 192.0.2.1" not in text
    assert "HostName 192.0.2.2" in text and "HostName 192.0.2.3" in text
    assert os.stat(path).st_mode & 0o777 == 0o600
    if shutil.which("ssh"):
        for host, ip, port in [("training", "192.0.2.3", 2203), ("analysis", "192.0.2.2", 2202)]:
            result = subprocess.run(["ssh", "-G", "-F", str(path), host], capture_output=True, text=True, check=True)
            assert f"hostname {ip}\n" in result.stdout
            assert f"port {port}\n" in result.stdout


def test_shared_aliases_comments_and_wildcards_are_preserved(manager, tmp_path):
    path = tmp_path / "config"
    path.write_text("# existing\nHost runpod legacy\n  HostName 192.0.2.1\nHost *\n  User fallback\nMatch host special\n  Port 23\n")
    manager._write_ssh_config("192.0.2.2", 22, False, str(path))
    text = path.read_text()
    assert "Host legacy\n  HostName 192.0.2.1" in text
    assert "# existing" in text and "Host *\n  User fallback" in text
    assert "Match host special\n  Port 23" in text
    assert text.index("Host runpod") < text.index("Host *")


def test_global_directives_remain_before_all_host_blocks(manager, tmp_path):
    path = tmp_path / "config"
    path.write_text("IdentityFile ~/.ssh/shared_key\nHost old\n  HostName 192.0.2.1\n")
    manager._write_ssh_config("192.0.2.2", 22, False, str(path))
    assert path.read_text().startswith("IdentityFile ~/.ssh/shared_key\nHost runpod\n")


@pytest.mark.parametrize("host", ["", "*", "!runpod", "two hosts", "runpod\nProxyCommand malicious", "-option"])
def test_invalid_alias_fails_before_pod_creation(manager, host):
    with pytest.raises(ValueError, match="ssh_host"):
        manager.create(ssh_host=host)
