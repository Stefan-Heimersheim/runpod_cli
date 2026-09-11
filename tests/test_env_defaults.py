from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager, env_default


def test_cli_flag_wins_over_env(monkeypatch):
    monkeypatch.setenv("RPC_DEFAULT_RUNTIME", "240")
    assert env_default(30, "RPC_DEFAULT_RUNTIME", 60) == 30


def test_env_wins_over_fallback_and_casts_to_int(monkeypatch):
    monkeypatch.setenv("RPC_DEFAULT_RUNTIME", "240")
    assert env_default(None, "RPC_DEFAULT_RUNTIME", 60) == 240


def test_fallback_when_env_unset(monkeypatch):
    monkeypatch.delenv("RPC_DEFAULT_RUNTIME", raising=False)
    assert env_default(None, "RPC_DEFAULT_RUNTIME", 60) == 60


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("false", False), ("no", False)])
def test_bool_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("RPC_DEFAULT_FORWARD_AGENT", value)
    assert env_default(None, "RPC_DEFAULT_FORWARD_AGENT", False) is expected


def test_bool_rejects_garbage(monkeypatch):
    monkeypatch.setenv("RPC_DEFAULT_FORWARD_AGENT", "maybe")
    with pytest.raises(ValueError, match="RPC_DEFAULT_FORWARD_AGENT"):
        env_default(None, "RPC_DEFAULT_FORWARD_AGENT", False)


@pytest.fixture
def manager():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pods.return_value = [{"id": "existing"}]
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU"
    return manager


def test_create_uses_default_ssh_public_key_path(manager, monkeypatch, tmp_path):
    key_file = tmp_path / "id_ed25519.pub"
    key_file.write_text("ssh-ed25519 AAAAtestkey user@host\n")
    monkeypatch.setenv("RPC_DEFAULT_SSH_PUBLIC_KEY_PATH", str(key_file))
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test")
    manager._api.get_pub_key.assert_not_called()
    assert manager._api.create_pod.call_args.kwargs["env"] == {"PUBLIC_KEY": "ssh-ed25519 AAAAtestkey user@host"}


def test_create_uses_default_runtime_in_docker_args(manager, monkeypatch):
    monkeypatch.setenv("RPC_DEFAULT_RUNTIME", "240")
    manager._api.get_pub_key.return_value = ""
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test")
    assert f"sleep {240 * 60}" in manager._api.create_pod.call_args.kwargs["docker_args"]
