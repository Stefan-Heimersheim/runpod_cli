# RunPod CLI Manager

A command-line tool for managing RunPod instances via the RunPod API, based on 
[ApolloResearch/runpod_cli](https://github.com/ApolloResearch/runpod_cli). This
version makes some larger changes:
- Uses RunPod's S3 API instead of manually copying files to the network volume
- Installs a list of selected Python packages to the pod on startup (with `--system`)
  - Intended use-case: Create environments with `--system-site-packages`.
  - This allows us use the previously mentioned system packages (installed on the faster (ephemeral) container disk).
- Quality-of-life improvements:
  - Set selected global git settings (including `user.name` and `user.email` if configured in `.env`)
  - Automatically adds SSH key to known_hosts (obtained via secure https S3 API)
  - Installs Claude Code & Codex on pod startup
  - By default set the pod name based on username and GPU type
  - Allow using GPU ID or display name to specify the GPU type

These changes rely on [RunPod's S3 API](https://docs.runpod.io/serverless/storage/s3-api), which is currently only available in some regions. Make sure your network volume is in one of the following regions:
- EUR-IS-1
- EU-RO-1
- EU-CZ-1
- US-KS-2


**Security note:** Your Runpod keys will be stored on the pod (`/root/.runpod.env`) and is accessible to anyone that can log in to your pod. This includes team members whose SSH keys are added to your Runpod team account [settings](https://console.runpod.io/user/settings).

## Setup

### Installation

#### Option 1: Install as a UV tool (recommended)

1. Clone this repository
2. Install as a UV tool:
   ```bash
   cd runpod_cli
   uv tool install -e .
   ```
3. Ensure UV's bin directory is on your PATH:
   ```bash
   uv tool update-shell
   ```
   Then restart your terminal or re-source your shell config.

#### Option 2: Install with pip

1. Clone this repository
2. Install into current Python environment:
   ```bash
   cd runpod_cli
   pip install -e .
   ```

#### Option 3: Install requirements only (not recommended)

1. Clone this repository
2. Install dependencies into current Python environment:
   ```bash
   cd runpod_cli
   pip install -r requirements.txt
   ```
3. In this case you need to use `python src/runpod_cli/cli.py` to run the CLI.

### API keys

1. Create a RunPod [network-volume](https://docs.runpod.io/pods/storage/create-network-volumes). Choose a region from the S3-supported regions; pick one that has availability for your preferred GPU types.

2. [Optional but recommended] Add this line to the top of your `~/.ssh/config`:
```
Include ~/.ssh/config.runpod_cli
```

2. Copy `.env.example` to `~/.config/runpod_cli/.env` and add your RunPod credentials:
```bash
mkdir -p ~/.config/runpod_cli
cp .env.example ~/.config/runpod_cli/.env
```

3. Add your RunPod credentials to the `.env` file. *Note: If you use a RunPod team, the team account needs to create those API keys.*
   - **RUNPOD_API_KEY**: Your RunPod API key
   - **RUNPOD_NETWORK_VOLUME_ID**: Your RunPod network volume ID
   - **RUNPOD_S3_ACCESS_KEY_ID**: S3 access key for your RunPod network volume
   - **RUNPOD_S3_SECRET_KEY**: S3 secret key for your RunPod network volume
   - [Optional] **GIT_NAME and GIT_EMAIL**: Setting global git config on pod startup

## Usage
* `rpc create` - Create a new pod (default: 1 hour runtime, 1 A4000 GPU)
* `rpc list` - List all pods in your account
* `rpc terminate` - Terminate a specific pod

Instead of the `rpc` command, you can also use `python -m runpod_cli` to run the CLI.

### Command Examples
Create a dev pod with one A4000 GPU for 1 hour (these are also the default values):

```bash
rpc create --gpu_type "RTX A4000" --runtime 60
```

Create a dev pod with two A100 GPUs for 4 hours (adjust PCIe to SXM if needed):
```bash
rpc create --gpu_type "A100 PCIe" --runtime 240 --gpu_count 2
```

## Python environment recommendations

I recommend using [virtualenv](https://virtualenv.pypa.io/en/latest/) (pre-installed on the pod)
or [python -m venv](https://docs.python.org/3/library/venv.html) (slower) to create a virtual environment.
```bash
virtualenv venv  --system-site-packages
source venv/bin/activate
```
or
```bash
python -m venv venv --system-site-packages
source venv/bin/activate
```
or
```bash
uv venv venv --python 3.11 --system-site-packages
source venv/bin/activate
```
With the pre-installed system packages, commands like `pip install transformer_lens` will finish in seconds.

Note: [uv](https://docs.astral.sh/uv/) handles `--system-site-packages`
[differently](https://docs.astral.sh/uv/reference/cli/#uv-venv--system-site-packages)
and `uv pip install` will ignore system packages and reinstall dependencies
into the environment.


## Known issues
Python Fire has a known issue (fixed & merged on [GitHub](https://github.com/google/python-fire/pull/588/files) but not released on PyPI yet)
with ipython==9.0 which will produce the following error:
```
ERROR  | Uncaught exception | <class 'TypeError'>; Inspector.__init__() missing 1 required keyword-only argument: 'theme_name';
```

## Future features & improvements
- Allow for custom bashrc
- Allow for persistent bash history
- Set UV_LINK_MODE=copy or move the uv cache
- Find a better way to wait for ssh keys to be generated than `time.sleep(5)`
- Allow user to configre an SSH_PUBLIC_KEY_PATH in .env
- Find a way for users to share a network volume with other users by making the `/network/runpodcli` directory user-specific
- Pre-install VS Code / Cursor server
