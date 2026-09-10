import os
import subprocess
from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager
from runpod_cli.utils import get_setup_user


def test_shell_settings_are_sourced_before_installation(tmp_path):
    custom = tmp_path / "custom ' settings.sh"
    custom.write_text('export CUSTOM_TEST="$HOME/config"\n')
    _, script = get_setup_user(str(tmp_path), "test@example.com", "Test", str(custom))
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    setup = script.split("# Git configuration")[0]
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=setup + '\ntest "$CUSTOM_TEST" = "$HOME/config"\n',
                   text=True, env=env, check=True)
    bashrc = (tmp_path / ".bashrc").read_text()
    subprocess.run(["bash"], input=bashrc + '\ntest "$CUSTOM_TEST" = "$HOME/config"\n',
                   text=True, env=env, check=True)


def test_no_custom_file_keeps_default_setup():
    _, script = get_setup_user("/network/test", "test@example.com", "Test")
    assert "CUSTOM_BASHRC_SETUP" not in script
    assert '>> "$HOME/.bashrc"' not in script


def test_missing_file_fails_before_any_remote_operations(tmp_path):
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._s3 = Mock()
    with pytest.raises(FileNotFoundError):
        manager.create(bashrc=str(tmp_path / "missing"))
    assert not manager._api.mock_calls
    assert not manager._s3.mock_calls


def test_cli_file_overrides_environment_and_uploads_literal_content(tmp_path, monkeypatch):
    custom = tmp_path / "custom.sh"
    content = 'export CUSTOM_TEST="$HOME/config"\n'
    custom.write_text(content)
    monkeypatch.setenv("RUNPOD_BASHRC", str(tmp_path / "missing"))
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pub_key.return_value = ""
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU"
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test", bashrc=str(custom))
    uploads = {call.kwargs["Key"]: call.kwargs["Body"] for call in manager._s3.put_object.call_args_list}
    assert uploads[".tmp_test/custom_bashrc.sh"] == content.encode()
    assert b"source /network/.tmp_test/custom_bashrc.sh" in uploads[".tmp_test/setup_user.sh"]
