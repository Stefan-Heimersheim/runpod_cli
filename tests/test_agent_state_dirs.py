from runpod_cli.utils import get_install, get_setup_user

def test_agent_state_dirs_are_created():
    _, script = get_setup_user("/network/test", "", "", local_user="alice")
    assert "mkdir -p /workspace/.claude_alice /workspace/.codex_alice" in script
