FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04

# Don't ask questions when installing packages
ENV DEBIAN_FRONTEND=noninteractive

RUN apt update -y && apt install -y sudo git vim ssh net-tools htop curl zip unzip tmux rsync libopenmpi-dev iputils-ping make fzf

# Add a user with sudo permissions
RUN useradd --uid 1000 --shell /bin/bash user --groups sudo --create-home && \
    echo 'user ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Install Miniconda
RUN curl -O https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
    bash Miniconda3-latest-Linux-x86_64.sh -b -p /opt/conda && \
    rm Miniconda3-latest-Linux-x86_64.sh

# Make conda available to all users
ENV PATH="/opt/conda/bin:${PATH}"
RUN chmod -R 777 /opt/conda

USER user
WORKDIR /home/user

# Setup conda for the user with python=3.12
RUN conda init bash && \
    conda create -n py312 python=3.12 -y && \
    echo "conda activate py312" >> ~/.bashrc

# # to avoid problems installing mpi4py with python
# # see https://github.com/ContinuumIO/anaconda-issues/issues/11152
# # Note that this will prevent one from installing mpi4py inside a conda environment
# RUN rm /home/user/.conda/envs/py312/compiler_compat/ld

# Set up fzf key bindings and fuzzy completion
RUN eval "$(fzf --bash)"

# Add vim bindings for tmux
RUN echo "set-window-option -g mode-keys vi" >> ~/.tmux.conf && \
    echo "bind-key -T copy-mode-vi v send -X begin-selection" >> ~/.tmux.conf && \
    echo "bind-key -T copy-mode-vi y send -X select-line" >> ~/.tmux.conf && \
    echo "bind-key -T copy-mode-vi r send -X copy-pipe-and-cancel 'xclip -in -selection clipboard'" >> ~/.tmux.conf

# Setup AWS-CLI
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    sudo ./aws/install && \
    rm -rf awscliv2.zip aws

# Set default command to bash
CMD ["/bin/bash"]