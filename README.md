# RunPod CLI Manager

A command-line tool for managing RunPod instances via the RunPod API, based on 
[ApolloResearch/runpod_cli](https://github.com/ApolloResearch/runpod_cli). This
version includes some opinionated changes, including:
- Uses RunPod's S3 API instead of manually copying files to the network volume
- Installs a list of selected Python packages to the pod on startup (with `--system`)
  - Intended use-case: Create environments with `uv venv --python 3.11 --system-site-packages`
  - This allows us to use system packages installed on the faster (ephemeral) container disk
- Quality-of-life improvements:
  - Set selected global git settings (including `user.name` and `user.email` if configured in `.env`)
  - Automatically adds SSH key to known_hosts (obtained via secure https S3 API)
  - Installs Claude Code & Codex on pod startup
  - By default set the pod name based on username and GPU type
  - Allow using GPU ID or display name to specify the GPU type

These changes rely on [RunPod's S3 API](https://docs.runpod.io/serverless/storage/s3-api), which is currently only available in the following regions:
- EUR-IS-1
- EU-RO-1
- EU-CZ-1
- US-KS-2

Make sure your network volume is in one of these regions.

## Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a RunPod [network-volume](https://docs.runpod.io/pods/storage/create-network-volumes). Choose a region from the S3-supported regions; pick one that has availability for your preferred GPU types.
3. Copy `.env.example` to `.env` and add your RunPod credentials:
   - **RUNPOD_API_KEY**: Your RunPod API key
   - **RUNPOD_NETWORK_VOLUME_ID**: Your RunPod network volume ID
   - **RUNPOD_S3_ACCESS_KEY_ID**: S3 access key for your RunPod network volume
   - **RUNPOD_S3_SECRET_KEY**: S3 secret key for your RunPod network volume

**Security note:** Your Runpod keys will be stored on the pod (`/root/.runpod.env`) and is accessible to anyone that can log in to your pod. This includes team members whose SSH keys are added to your Runpod team account [settings](https://console.runpod.io/user/settings).

## Usage

The CLI offers the following high-level commands; though
my focus is on creating pods.

* Create a pod (fully parameterised)
* List pods
* Inspect a pod
* Terminate a pod

### Quick examples

Create a dev pod with one A40 GPU for **1 hour**:

```bash
python cli.py create_pod \
    --name "mary-pod" \
    --gpu_type "NVIDIA A40" \
    --runtime 60
```

# Future features & improvements
- Allow for custom bashrc
- Allow for persistent bash history
- Set UV_LINK_MODE=copy or move the uv cache
- Find a better way to wait for ssh keys to be generated than `time.sleep(5)`
- Turn into a package usable from the cmdline, with a config file in $XDG_CONFIG_HOME/runpod-cli/config.yaml$