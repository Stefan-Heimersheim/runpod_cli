import textwrap
import shlex
from typing import Optional, Tuple

# Default Docker image for pods
DEFAULT_IMAGE_NAME = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"

# Shell scripts to load onto the pod
def get_setup_root(runpodcli_path: str, volume_mount_path: str) -> Tuple[str, str]:
    return "setup_root.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) setup_root.sh ==="

        echo "Setting up system environment..."

        useradd --uid 1000 --shell /bin/bash user --groups sudo --create-home
        # Set NNSIGHT_LOG_PATH to avoid https://github.com/ndif-team/nnsight/issues/495
        echo "export NNSIGHT_LOG_PATH=/root/.local/state/nnsight" >> /root/.profile
        echo "export NNSIGHT_LOG_PATH=/home/user/.local/state/nnsight" >> /home/user/.profile
        chown user:user /home/user/.profile
        mkdir -p  /home/user/.ssh/
        cat /root/.ssh/authorized_keys >> /home/user/.ssh/authorized_keys
        chown -R user:user /home/user/.ssh

        if [[ VOLUME_MOUNT_PATH != "/workspace" ]]; then
            rmdir /workspace
            ln -s VOLUME_MOUNT_PATH /workspace
        fi

        echo 'user ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
        echo "export HF_HOME=/workspace/hf_home/" >> /home/user/.bashrc
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/user/.bashrc
        chmod a+x RUNPODCLI_PATH/terminate_pod.sh
        ln -s RUNPODCLI_PATH/terminate_pod.sh /usr/local/bin/terminate_pod

        echo "...system setup completed!"
    """.replace("RUNPODCLI_PATH", runpodcli_path).replace("VOLUME_MOUNT_PATH", volume_mount_path)
    )


def get_install(runpodcli_path: str) -> Tuple[str, str]:
    return "install.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) install.sh ==="

        echo "Installing agents, system packages, and tools..."

        # Urgent tools first. Try without the slow apt-get update: it fails
        # fast when the image ships no package lists, and skips minutes of
        # mirror fetches when it does ship them.
        apt-get install -y tmux git rsync curl sudo \
            || { apt-get update && apt-get install -y tmux git rsync curl sudo; }

        # Install Claude Code and Codex for the pod user next (they install
        # into ~/.local), so agents are usable before the slower apt work
        su -c 'curl -fsSL https://claude.ai/install.sh | bash' user
        su -c 'curl -fsSL https://chatgpt.com/codex/install.sh | sh' user

        apt-get update
        apt-get upgrade -y
        apt-get install -y vim ssh net-tools htop zip unzip libopenmpi-dev iputils-ping make fzf restic ripgrep wget pandoc poppler-utils pigz bzip2 nano locales

        # Install gh
        out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            && mkdir -p -m 755 /etc/apt/keyrings \
            && cat $out > /etc/apt/keyrings/githubcli-archive-keyring.gpg \
            && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
            && mkdir -p -m 755 /etc/apt/sources.list.d \
            && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
            && apt update \
            && apt install gh -y

        # Install Python packages using uv
        pip install uv
        uv pip install --system --compile-bytecode ipykernel kaleido nbformat numpy scipy scikit-learn scikit-image transformers datasets torchvision pandas matplotlib seaborn plotly jaxtyping einops tqdm ruff basedpyright umap-learn ipywidgets virtualenv  pytest git+https://github.com/callummcdougall/eindex.git transformer_lens nnsight
        # For plotly (kaleido) png export
        apt-get install -y libnss3 libatk-bridge2.0-0 libcups2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 libasound2
        plotly_get_chrome -y
        # Create a virtual environment for the pod user
        su -c 'uv venv ~/.venv --python $(python --version | cut -d" " -f2 | cut -d. -f1-2) --system-site-packages' user

        echo "...installs completed!"
    """.replace("RUNPODCLI_PATH", runpodcli_path)
    )


def get_setup_user(
    runpodcli_path: str, git_email: str, git_name: str, bashrc_line: Optional[str] = None, local_user: str = "user"
) -> Tuple[str, str]:
    bashrc_setup = f"echo {shlex.quote(str(bashrc_line))} >> ~/.bashrc" if bashrc_line else ""
    return "setup_user.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) setup_user.sh ==="

        echo "Setting up user environment..."

        # Persist shell history and coding-agent state on the network volume,
        # so they survive pod termination. /workspace always points at the
        # volume (setup_root.sh symlinks it when the mount path differs).
        # Every pod runs as "user", so the files are keyed by the local
        # username of the pod creator to keep team members' state separate.
        echo 'export HISTFILE=/workspace/.bash_history_LOCAL_USER' >> ~/.bashrc
        echo 'export HISTSIZE=10000000' >> ~/.bashrc
        echo 'export HISTFILESIZE=10000000' >> ~/.bashrc
        echo 'shopt -s histappend' >> ~/.bashrc
        echo 'PROMPT_COMMAND="history -a; $PROMPT_COMMAND"' >> ~/.bashrc
        echo 'export CLAUDE_CONFIG_DIR=/workspace/.claude_LOCAL_USER' >> ~/.bashrc
        echo 'export CODEX_HOME=/workspace/.codex_LOCAL_USER' >> ~/.bashrc
        # uv cannot hardlink from its container-disk cache into venvs on the
        # network volume; default to copying instead of warning every install.
        echo 'export UV_LINK_MODE=copy' >> ~/.bashrc

        CUSTOM_BASHRC_SETUP

        # Git configuration, written directly so it needs no git binary
        # (skip empty identity values, which would break committing on the pod)
        if [ -n "GIT_EMAIL" ] || [ -n "GIT_NAME" ]; then
            echo '[user]' >> ~/.gitconfig
            if [ -n "GIT_EMAIL" ]; then
                echo '    email = GIT_EMAIL' >> ~/.gitconfig
            fi
            if [ -n "GIT_NAME" ]; then
                echo '    name = GIT_NAME' >> ~/.gitconfig
            fi
        fi
        echo '[init]' >> ~/.gitconfig
        echo '    defaultBranch = main' >> ~/.gitconfig

        echo "...user setup completed!"
    """.replace("RUNPODCLI_PATH", runpodcli_path)
        .replace("GIT_EMAIL", git_email)
        .replace("GIT_NAME", git_name)
        .replace("CUSTOM_BASHRC_SETUP", bashrc_setup)
        .replace("LOCAL_USER", local_user)
    )


def get_start(runpodcli_path: str) -> Tuple[str, str]:
    return "start_pod.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        # Adapted from https://github.com/runpod/containers/blob/main/container-template/start_pod.sh

        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) start_pod.sh ==="
        set -e  # exit the script if any line fails

        setup_ssh() {
            if [[ $PUBLIC_KEY ]]; then
                echo "Setting up SSH..."
                mkdir -p ~/.ssh
                echo "$PUBLIC_KEY" >> ~/.ssh/authorized_keys
                chmod 700 -R ~/.ssh

                if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
                    ssh-keygen -t rsa -f /etc/ssh/ssh_host_rsa_key -q -N ''
                    echo "RSA key fingerprint:"
                    ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub
                    cp /etc/ssh/ssh_host_rsa_key.pub RUNPODCLI_PATH/ssh_rsa_host_key
                fi

                if [ ! -f /etc/ssh/ssh_host_dsa_key ]; then
                    # DSA was removed in OpenSSH >= 9.8; don't let set -e kill the pod setup
                    if ssh-keygen -t dsa -f /etc/ssh/ssh_host_dsa_key -q -N ''; then
                        echo "DSA key fingerprint:"
                        ssh-keygen -lf /etc/ssh/ssh_host_dsa_key.pub
                        cp /etc/ssh/ssh_host_dsa_key.pub RUNPODCLI_PATH/ssh_dsa_host_key
                    else
                        echo "DSA host key generation not supported, skipping"
                    fi
                fi

                if [ ! -f /etc/ssh/ssh_host_ecdsa_key ]; then
                    ssh-keygen -t ecdsa -f /etc/ssh/ssh_host_ecdsa_key -q -N ''
                    echo "ECDSA key fingerprint:"
                    ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub
                    cp /etc/ssh/ssh_host_ecdsa_key.pub RUNPODCLI_PATH/ssh_ecdsa_host_key
                fi

                if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
                    ssh-keygen -t ed25519 -f /etc/ssh/ssh_host_ed25519_key -q -N ''
                    echo "ED25519 key fingerprint:"
                    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
                    cp /etc/ssh/ssh_host_ed25519_key.pub RUNPODCLI_PATH/ssh_ed25519_host_key
                fi

                service ssh start

                echo "SSH host keys:"
                for key in /etc/ssh/*.pub; do
                    echo "Key: $key"
                    ssh-keygen -lf $key
                done
            fi
        }

        export_env_vars() {
            echo "Exporting environment variables..."
            printenv | grep -E '^RUNPOD_|^PATH=|^_=' | awk -F = '{ print "export " $1 "=\"" $2 "\"" }' >> ~/.runpod_env
            echo 'source ~/.runpod_env' >> ~/.bashrc
        }

        # Fast setup first (accounts, bashrc, git config — seconds), then the
        # slow installs (apt upgrade, agent CLIs, Python packages — minutes),
        # so early SSH logins get a fully configured shell.
        setup_ssh
        export_env_vars
        bash RUNPODCLI_PATH/setup_root.sh
        su -c "bash RUNPODCLI_PATH/setup_user.sh" user
        echo "Fast setup finished, pod is ready to log in; installs continue..."
        bash RUNPODCLI_PATH/install.sh

        echo "Start script(s) finished, pod is ready to use."
    """.replace("RUNPODCLI_PATH", runpodcli_path)
    )


def get_terminate(runpodcli_path: str) -> Tuple[str, str]:
    return "terminate_pod.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) terminate_pod.sh ==="

        if [ "$(id -u)" -ne 0 ]; then
            echo "Not running as root, attempting to copy runpod env"
            sudo cp /root/.runpod_env /home/user/.runpod_env
            sudo chown user /home/user/.runpod_env
            source /home/user/.runpod_env
        else
            source /root/.runpod_env
        fi

        echo "Requesting pod termination..."
        curl --request DELETE \
        --header "Authorization: Bearer ${RUNPOD_API_KEY}" \
        --url "https://api.runpod.io/v2/pods/${RUNPOD_POD_ID}"
    """.replace("RUNPODCLI_PATH", runpodcli_path)
    )
