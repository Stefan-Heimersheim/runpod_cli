import os
import re
import subprocess

import pytest

from runpod_cli.utils import get_install, get_setup_root, get_setup_user, get_start, get_terminate


@pytest.mark.parametrize(
    "name,script",
    [
        get_setup_root("/network/test", "/network"),
        get_setup_user("/network/test", "test@example.com", "Test"),
        get_install("/network/test"),
        get_start("/network/test"),
        get_terminate("/network/test"),
    ],
)
def test_scripts_pass_bash_syntax_check(name, script):
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


def test_fast_setup_runs_before_slow_installs():
    _, start = get_start("/network/test")
    order = [start.index(step) for step in
             ["setup_root.sh", "setup_user.sh", "ready to log in", "install.sh"]]
    assert order == sorted(order)
    # The slow work lives in the install scripts, not the setup scripts
    _, setup_root = get_setup_root("/network/test", "/network")
    _, setup_user = get_setup_user("/network/test", "test@example.com", "Test")
    _, install = get_install("/network/test")
    # the fast phase runs no apt (and no git) at all, so it finishes in seconds
    assert "apt-get" not in setup_root and "apt-get" not in setup_user
    assert "git config" not in setup_user  # .gitconfig is written directly
    assert "apt-get upgrade" in install
    for slow in ["claude.ai/install.sh", "uv pip install", "plotly_get_chrome", "apt install gh"]:
        assert slow not in setup_user and slow in install
    # user-level pieces run as the pod user via su, everything else as root
    assert "su -c 'curl -fsSL https://claude.ai/install.sh | bash' user" in install
    assert not re.search(r"^\s*sudo ", install, re.M)  # root needs no sudo prefix
    # install.sh order: urgent tools, then agents, then the remaining apt work
    assert install.index("tmux git rsync curl sudo") < install.index("claude.ai/install.sh") < install.index("apt-get upgrade")
    # urgent tools try the image's package lists before paying for apt-get update
    assert "|| { apt-get update && apt-get install -y tmux git rsync curl sudo; }" in install


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
    return script.split("# Git configuration")[1]


def test_empty_git_identity_is_not_configured(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    subprocess.run(["bash"], input=git_config_section("", ""), text=True, env=env, check=True)
    gitconfig = (tmp_path / ".gitconfig").read_text()
    assert "[user]" not in gitconfig
    assert "defaultBranch = main" in gitconfig


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
