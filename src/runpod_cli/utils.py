import textwrap
from typing import Tuple

# Default Docker image for pods
DEFAULT_IMAGE_NAME = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"

# GPU display name to ID mapping from https://docs.runpod.io/references/gpu-types
GPU_DISPLAY_NAME_TO_ID = {
    "MI300X": "AMD Instinct MI300X OAM",
    "A100 PCIe": "NVIDIA A100 80GB PCIe",
    "A100 SXM": "NVIDIA A100-SXM4-80GB",
    "A30": "NVIDIA A30",
    "A40": "NVIDIA A40",
    "B200": "NVIDIA B200",
    "RTX 3070": "NVIDIA GeForce RTX 3070",
    "RTX 3080": "NVIDIA GeForce RTX 3080",
    "RTX 3080 Ti": "NVIDIA GeForce RTX 3080 Ti",
    "RTX 3090": "NVIDIA GeForce RTX 3090",
    "RTX 3090 Ti": "NVIDIA GeForce RTX 3090 Ti",
    "RTX 4070 Ti": "NVIDIA GeForce RTX 4070 Ti",
    "RTX 4080": "NVIDIA GeForce RTX 4080",
    "RTX 4080 SUPER": "NVIDIA GeForce RTX 4080 SUPER",
    "RTX 4090": "NVIDIA GeForce RTX 4090",
    "RTX 5080": "NVIDIA GeForce RTX 5080",
    "RTX 5090": "NVIDIA GeForce RTX 5090",
    "H100 SXM": "NVIDIA H100 80GB HBM3",
    "H100 NVL": "NVIDIA H100 NVL",
    "H100 PCIe": "NVIDIA H100 PCIe",
    "H200 SXM": "NVIDIA H200",
    "L4": "NVIDIA L4",
    "L40": "NVIDIA L40",
    "L40S": "NVIDIA L40S",
    "RTX 2000 Ada": "NVIDIA RTX 2000 Ada Generation",
    "RTX 4000 Ada": "NVIDIA RTX 4000 Ada Generation",
    "RTX 5000 Ada": "NVIDIA RTX 5000 Ada Generation",
    "RTX 6000 Ada": "NVIDIA RTX 6000 Ada Generation",
    "RTX A2000": "NVIDIA RTX A2000",
    "RTX A4000": "NVIDIA RTX A4000",
    "RTX A4500": "NVIDIA RTX A4500",
    "RTX A5000": "NVIDIA RTX A5000",
    "RTX A6000": "NVIDIA RTX A6000",
    "RTX PRO 6000": "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
    "V100 FHHL": "Tesla V100-FHHL-16GB",
    "Tesla V100": "Tesla V100-PCIE-16GB",
    "V100 SXM2": "Tesla V100-SXM2-16GB",
}
GPU_ID_TO_DISPLAY_NAME = {v: k for k, v in GPU_DISPLAY_NAME_TO_ID.items()}


# Shell scripts to load onto the pod
def get_setup_root(runpodcli_path: str, volume_mount_path: str) -> Tuple[str, str]:
    return "setup_root.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) setup_root.sh ==="

        echo "Setting up system environment..."

        useradd --uid 1000 --shell /bin/bash user --groups sudo --create-home
        mkdir -p  /home/user/.ssh/
        touch /home/user/.ssh/authorized_keys
        chown user /home/user/.ssh/authorized_keys
        cat /root/.ssh/authorized_keys >> /home/user/.ssh/authorized_keys

        if [[ VOLUME_MOUNT_PATH != "/workspace" ]]; then
            rmdir /workspace
            ln -s VOLUME_MOUNT_PATH /workspace
        fi

        apt-get update
        apt-get upgrade -y
        apt-get install -y sudo git vim ssh net-tools htop curl zip unzip tmux rsync libopenmpi-dev iputils-ping make fzf restic ripgrep wget pandoc poppler-utils pigz bzip2 nano
        echo 'user ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
        echo "export HF_HOME=/workspace/hf_home/" >> /home/user/.bashrc
        chmod a+x RUNPODCLI_PATH/terminate_pod.sh

        echo "...system setup completed!"
    """.replace("RUNPODCLI_PATH", runpodcli_path).replace("VOLUME_MOUNT_PATH", volume_mount_path)
    )


def get_setup_user(runpodcli_path: str, git_email: str, git_name: str) -> Tuple[str, str]:
    return "setup_user.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt 2>&1 # logging
        echo "=== $(date -Iseconds) setup_user.sh ==="

        echo "Setting up user environment..."

        # Git configuration
        git config --global user.email GIT_EMAIL
        git config --global user.name GIT_NAME
        git config --global init.defaultBranch main

        # Install Node.js and npm packages
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
        nvm install 22
        nvm use 22
        # Install Claude Code and Codex
        npm install -g @anthropic-ai/claude-code
        npm install -g @openai/codex

        # Install gh
        (type -p wget >/dev/null || (sudo apt update && sudo apt-get install wget -y)) \
            && sudo mkdir -p -m 755 /etc/apt/keyrings \
            && out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            && cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
            && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
            && sudo mkdir -p -m 755 /etc/apt/sources.list.d \
            && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
            && sudo apt update \
            && sudo apt install gh -y

        # Install Python packages using uv
        sudo pip install uv
        sudo uv pip install ipykernel kaleido nbformat numpy scipy scikit-learn pandas matplotlib seaborn plotly jaxtyping einops tqdm ruff basedpyright umap-learn ipywidgets virtualenv git+https://github.com/callummcdougall/eindex.git --system

        echo "...user setup completed!"
    """.replace("RUNPODCLI_PATH", runpodcli_path)
        .replace("GIT_EMAIL", git_email)
        .replace("GIT_NAME", git_name)
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
                    ssh-keygen -t dsa -f /etc/ssh/ssh_host_dsa_key -q -N ''
                    echo "DSA key fingerprint:"
                    ssh-keygen -lf /etc/ssh/ssh_host_dsa_key.pub
                    cp /etc/ssh/ssh_host_dsa_key.pub RUNPODCLI_PATH/ssh_dsa_host_key
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

        setup_ssh
        export_env_vars
        bash RUNPODCLI_PATH/setup_root.sh
        su -c "bash RUNPODCLI_PATH/setup_user.sh" user

        echo "Start script(s) finished, pod is ready to use."
    """.replace("RUNPODCLI_PATH", runpodcli_path)
    )


def get_terminate(runpodcli_path: str) -> Tuple[str, str]:
    return "terminate_pod.sh", textwrap.dedent(
        r"""
        #!/bin/bash
        exec >> RUNPODCLI_PATH/log.txt | tee -a RUNPODCLI_PATH/log.txt 2>&1 # logging
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
        curl --request POST \
        --header 'content-type: application/json' \
        --url "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
        --data "{\"query\": \"mutation { podTerminate(input: {podId: \\\"${RUNPOD_POD_ID}\\\"}) }\"}"
    """.replace("RUNPODCLI_PATH", runpodcli_path)
    )
