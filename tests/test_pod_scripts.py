import os
import shutil
import subprocess

import pytest

from runpod_cli.utils import get_setup_root, get_setup_user, get_start, get_terminate


@pytest.mark.parametrize(
    "name,script",
    [
        get_setup_root("/network/test", "/network"),
        get_setup_user("/network/test", "test@example.com", "Test"),
        get_start("/network/test"),
        get_terminate("/network/test"),
    ],
)
def test_scripts_pass_bash_syntax_check(name, script):
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


def test_terminate_logging_redirects_stderr_without_dead_tee():
    _, script = get_terminate("/network/test")
    assert "exec >> /network/test/log.txt 2>&1" in script
    assert "tee" not in script


def test_terminate_uses_rest_v2_with_key_in_header():
    _, script = get_terminate("/network/test")
    assert 'https://api.runpod.io/v2/pods/${RUNPOD_POD_ID}' in script
    assert "graphql" not in script
    assert "api_key=" not in script  # the key travels in the Authorization header, not the URL


def git_config_section(git_email, git_name):
    _, script = get_setup_user("/network/test", git_email, git_name)
    return script.split("# Git configuration")[1].split("# Install Claude Code")[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_empty_git_identity_is_not_configured(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=git_config_section("", ""), text=True, env=env, check=True)
    gitconfig = (tmp_path / ".gitconfig").read_text()
    assert "[user]" not in gitconfig
    assert "defaultBranch = main" in gitconfig


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_identity_is_configured_when_provided(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=git_config_section("test@example.com", "Test"), text=True, env=env, check=True)
    gitconfig = (tmp_path / ".gitconfig").read_text()
    assert "email = test@example.com" in gitconfig
    assert "name = Test" in gitconfig


def test_dsa_keygen_failure_does_not_abort_start_script():
    _, script = get_start("/network/test")
    # start_pod.sh runs under set -e; a plain ssh-keygen -t dsa call would
    # abort pod setup on OpenSSH >= 9.8, which removed DSA support
    assert "if ssh-keygen -t dsa" in script
