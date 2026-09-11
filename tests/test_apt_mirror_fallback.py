from runpod_cli.utils import get_install, get_setup_user

def test_install_configures_apt_mirror_fallback_before_first_apt_call():
    _, install = get_install("/network/test")
    # a stalled archive.ubuntu.com must not block the install for minutes:
    # short timeout, one retry, and a mirror list apt falls back through
    assert install.index("/etc/apt/mirrors.txt") < install.index("apt-get install")
    assert "mirror+file:/etc/apt/mirrors.txt" in install
    assert 'Acquire::http::Timeout "5";' in install and 'Acquire::Retries "1";' in install
    assert "/etc/apt/sources.list " in install and "/etc/apt/sources.list.d/ubuntu.sources" in install
    assert "http://archive.ubuntu.com/ubuntu/" in install  # Canonical stays first in the list
