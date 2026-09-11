import os
import subprocess
from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager
from runpod_cli.utils import get_setup_user


def test_bashrc_line_is_appended(tmp_path):
    line = 'export CUSTOM_TEST="$HOME/config"'
    _, script = get_setup_user("/network/test", "test@example.com", "Test", line)
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    setup = script.split("# Git configuration")[0]
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=setup, text=True, env=env, check=True)
    bashrc = (tmp_path / ".bashrc").read_text()
    # The custom line comes last so it can override the persistence defaults
    assert bashrc.strip().endswith(line)
    assert bashrc.index("export UV_LINK_MODE=copy") < bashrc.index(line)
    subprocess.run(["bash"], input=bashrc + '\ntest "$CUSTOM_TEST" = "$HOME/config"\n',
                   text=True, env=env, check=True)


def test_no_bashrc_line_keeps_default_setup(tmp_path):
    _, script = get_setup_user("/network/test", "test@example.com", "Test")
    assert "CUSTOM_BASHRC_SETUP" not in script
    setup = script.split("# Git configuration")[0]
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=setup, text=True, env=env, check=True)
    bashrc = (tmp_path / ".bashrc").read_text()
    for expected in [
        "export HISTFILE=/workspace/.bash_history",
        "shopt -s histappend",
        'PROMPT_COMMAND="history -a; $PROMPT_COMMAND"',
        "export CLAUDE_CONFIG_DIR=/workspace/.claude",
        "export CODEX_HOME=/workspace/.codex",
        "export UV_LINK_MODE=copy",
    ]:
        assert expected in bashrc


def test_cli_embeds_line_in_setup_script():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pub_key.return_value = ""
    manager._api.get_pods.return_value = [{"id": "existing"}]
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU"
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="CPU", name="test", bashrc_line="export CUSTOM_TEST=1")
    uploads = {call.kwargs["Key"]: call.kwargs["Body"] for call in manager._s3.put_object.call_args_list}
    assert b"echo 'export CUSTOM_TEST=1' >> ~/.bashrc" in uploads[".tmp_test/setup_user.sh"]
