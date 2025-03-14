#!/bin/bash
# Adapted from https://github.com/runpod/containers/blob/main/container-template/start.sh
set -e  # Exit the script if any statement returns a non-true return value

# ---------------------------------------------------------------------------- #
#                          Function Definitions                                #
# ---------------------------------------------------------------------------- #

# Start nginx service
start_nginx() {
    echo "Starting Nginx service..."
    sudo service nginx start
}

# Execute script if exists
execute_script() {
    local script_path=$1
    local script_msg=$2
    if [[ -f ${script_path} ]]; then
        echo "${script_msg}"
        bash ${script_path}
    fi
}

# Setup ssh
setup_ssh() {
    if [[ $PUBLIC_KEY ]]; then
        echo "Setting up SSH..."
        mkdir -p ~/.ssh
        echo "$PUBLIC_KEY" >> ~/.ssh/authorized_keys
        chmod 700 -R ~/.ssh

        if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
            sudo ssh-keygen -t rsa -f /etc/ssh/ssh_host_rsa_key -q -N ''
            echo "RSA key fingerprint:"
            sudo ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub
        fi

        if [ ! -f /etc/ssh/ssh_host_dsa_key ]; then
            sudo ssh-keygen -t dsa -f /etc/ssh/ssh_host_dsa_key -q -N ''
            echo "DSA key fingerprint:"
            sudo ssh-keygen -lf /etc/ssh/ssh_host_dsa_key.pub
        fi

        if [ ! -f /etc/ssh/ssh_host_ecdsa_key ]; then
            sudo ssh-keygen -t ecdsa -f /etc/ssh/ssh_host_ecdsa_key -q -N ''
            echo "ECDSA key fingerprint:"
            sudo ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub
        fi

        if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
            sudo ssh-keygen -t ed25519 -f /etc/ssh/ssh_host_ed25519_key -q -N ''
            echo "ED25519 key fingerprint:"
            sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
        fi

        sudo service ssh start

        echo "SSH host keys:"
        for key in /etc/ssh/*.pub; do
            echo "Key: $key"
            sudo ssh-keygen -lf $key
        done
    fi
}

# Export env vars
export_env_vars() {
    echo "Exporting environment variables..."
    printenv | grep -E '^RUNPOD_|^PATH=|^_=' | awk -F = '{ print "export " $1 "=\"" $2 "\"" }' >> ~/.runpod_env
    echo 'source ~/.runpod_env' >> ~/.bashrc
}

# Start jupyter lab
start_jupyter() {
    if [[ $JUPYTER_PASSWORD ]]; then
        echo "Starting Jupyter Lab..."
        mkdir -p /workspace && \
        cd / && \
        nohup python3.10 -m jupyter lab --allow-root --no-browser --port=8888 --ip=* --FileContentsManager.delete_to_trash=False --ServerApp.terminado_settings='{"shell_command":["/bin/bash"]}' --ServerApp.token=$JUPYTER_PASSWORD --ServerApp.allow_origin=* --ServerApp.preferred_dir=/workspace &> /jupyter.log &
        echo "Jupyter Lab started"
    fi
}

# Add prompt customization to bashrc
setup_custom_prompt() {
    echo "Setting up custom prompt..."
    cat << 'EOF' > ~/.custom_prompt
# Custom prompt
parse_git_branch() {
     git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/ (\1)/'
}

set_prompt() {
    local exit_code=$?
    local env_display=""
    
    # Check for Python virtual environment first
    if [ -n "$VIRTUAL_ENV" ]; then
        # Extract environment name from VIRTUAL_ENV path
        env_display="($(basename "$VIRTUAL_ENV"))"
    else
        # Fall back to conda environment check
        env_display="$(conda env list 2>/dev/null | grep '*' | awk '{print $1}' | sed 's/^/(/' | sed 's/$/)/g')"
    fi
    
    PS1="${env_display} \[\033[01;34m\]\w\[\033[00m\]\$(parse_git_branch) \$ "
    return $exit_code
}

PROMPT_COMMAND=set_prompt

if command -v fzf-share >/dev/null; then
  source "$(fzf-share)/key-bindings.bash"
  source "$(fzf-share)/completion.bash"
fi

# Function to attach to tmux session
tmux_attach() {
    tmux a -t "$1"
}

# Function to kill tmux session
tmux_kill() {
    tmux kill-session -t "$1"
}

# Create aliases for tmux attach and kill
for i in {0..9}; do
    alias "t$i"="tmux_attach $i"
    alias "kt$i"="tmux_kill $i"
done
EOF
    echo 'source ~/.custom_prompt' >> ~/.bashrc
}
# ---------------------------------------------------------------------------- #
#                               Main Program                                   #
# ---------------------------------------------------------------------------- #

# start_nginx

# execute_script "/pre_start.sh" "Running pre-start script..."

echo "Pod Started"

setup_ssh
# start_jupyter
export_env_vars

setup_custom_prompt

# execute_script "/post_start.sh" "Running post-start script..."

echo "Start script(s) finished, pod is ready to use."

