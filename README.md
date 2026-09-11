# RunPod CLI Manager

A command-line tool for managing RunPod instances via the RunPod API, based on 
[Apollo Research's original runpod_cli tool](https://github.com/ApolloResearch/runpod_cli/tree/legacy).

This version makes several changes:
- Uses **RunPod’s S3 API** to provision startup scripts & host keys (no manual volume setup).
- Uses the **official RunPod Docker image** by default (often faster to pull).
- Installs a curated set of **system Python packages** on startup using `uv --system` on the fast
  container disk, so your venvs can reuse them via `--system-site-packages`.
- Quality-of-life improvements:
  - Automatically adds pod **SSH host keys** to your local `known_hosts` (retrieved over HTTPS via S3).
  - **Persistent bash history** and **Claude Code / Codex state** (`CLAUDE_CONFIG_DIR`,
    `CODEX_HOME`) stored on the network volume, so history and logins survive pod termination.
  - Sets `UV_LINK_MODE=copy` on the pod, avoiding uv hardlink warnings for venvs on the network volume.
  - Optional global git config on pod (`GIT_NAME`, `GIT_EMAIL`).
  - Installs **Claude Code** and **Codex** on pod startup.
  - Defaults the pod name to `<username>-<gpu>`.
  - Allows **GPU display name or ID** (e.g. `"RTX A4000"` or `"NVIDIA RTX A4000"`).

⚠️ These changes rely on [RunPod’s S3 API](https://docs.runpod.io/serverless/storage/s3-api),
which is currently available only in some regions. Ensure your network volume is in one of:
`EUR-IS-1`, `EU-RO-1`, `EU-CZ-1`, `US-KS-2`.

🔒 **Security note:** Your RunPod keys are stored on the pod at `/root/.runpod_env` and are
accessible to anyone who can log in to the pod. This includes team members whose SSH keys are
added to your Runpod team account [settings](https://console.runpod.io/user/settings). (This
is not a change from the original runpod_cli, but worth keeping in mind.)

## Installation

Install the [latest published release](https://github.com/Stefan-Heimersheim/runpod_cli/releases/latest)
for regular use; the `latest` tag always points to it. The `main` branch may
contain development changes that have not been released yet.

```bash
git clone --branch latest https://github.com/Stefan-Heimersheim/runpod_cli.git
cd runpod_cli
```

Choose one installation method below from the cloned directory.

### Option 1: Install as a `uv` tool (recommended)

```bash
uv tool install -e .
uv tool update-shell   # ensure uv’s bin dir is on PATH
# restart shell or re-source your profile
```

### Option 2: Install with pip

```bash
pip install -e .
```

### Option 3: Install requirements only (not recommended, for backwards compatibility)

```bash
pip install -r requirements.txt
# In this case use: python src/runpod_cli/cli.py
```

## Configuration

1. Create a RunPod [network-volume](https://docs.runpod.io/pods/storage/create-network-volumes).
   Choose a region from the S3-supported regions; pick one that has availability for your preferred GPU types.

2. [Optional but recommended] Add this line to the top of your `~/.ssh/config`:
```
Include ~/.ssh/config.runpod_cli
```

2. Copy `.env.example` to `~/.config/runpod_cli/.env` and add your RunPod credentials:
```bash
mkdir -p ~/.config/runpod_cli
cp .env.example ~/.config/runpod_cli/.env
```

3. Fill the following variables in `~/.config/runpod_cli/.env`:
- `RUNPOD_API_KEY` – your RunPod API key
- `RUNPOD_NETWORK_VOLUME_ID` – your network volume ID
- `RUNPOD_S3_ACCESS_KEY_ID` – S3 access key for the volume
- `RUNPOD_S3_SECRET_KEY` – S3 secret key for the volume
- (Optional) `GIT_NAME`, `GIT_EMAIL` – global git config on the pod

*Note: If you use a RunPod team, the team account needs to create those API keys.*

## Usage

You can run the CLI either as:
- `rpc` (installed console script), or
- `python -m runpod_cli`

### Available commands
- `rpc create` — Create a pod (defaults: 1× **RTX A4000**, **60 minutes**).
- `rpc list` — List your pods.
- `rpc gpus` — List GPU names and IDs from RunPod's catalog.
- `rpc terminate` — Terminate a specific pod.
- `rpc reset` — Delete the SSH config files written by runpod_cli.

### Examples
Create a dev pod with one A4000 GPU for 1 hour (these are also the default values):

```bash
rpc create --gpu_type "RTX A4000" --runtime 60
```

Create a dev pod with two A100 GPUs for 4 hours (adjust PCIe to SXM if needed):
```bash
rpc create --gpu_type "A100 PCIe" --runtime 240 --gpu_count 2
```

## GPU catalog

GPU names and IDs come from RunPod's REST v2 `/catalog/gpus` endpoint, fetched
on each GPU lookup (a log line shows the request and how long it took, so slow
lookups are easy to spot). Exact names and IDs, case-insensitive unique
substrings, and numeric selectors such as `4090` are supported. Ambiguous
matches ask you to choose a full name or ID. CPU-only creation does not fetch
the catalog.

## Multiple pod SSH hosts

Each pod gets a numbered SSH alias: pod N is written to its own file
`~/.ssh/config.runpod_cli.N` as `runpodN`, the `runpod` alias always points
to the most recent pod (kept in `~/.ssh/config.runpod_cli.default`), and
`~/.ssh/config.runpod_cli` contains only `Include` lines for these files:

```bash
rpc create
rpc create
ssh runpod1  # first pod
ssh runpod   # most recent pod (same as runpod2)
```

`rpc reset` deletes all of these files and restarts the numbering at
`runpod1`; `rpc create` does this automatically when no pods exist. Use the
`Include ~/.ssh/config.runpod_cli` configuration described above. These files
are fully managed by runpod_cli, so manual edits may be overwritten.
`--update_ssh_config=False` disables local config changes.

## Custom pod shell settings

Pass a line to append to the pod user's `~/.bashrc`:

```bash
rpc create --bashrc_line='export PATH="$HOME/bin:$PATH"'
```

The line is written verbatim, so shell expressions such as `$HOME` expand on
the pod when an interactive shell starts. It is appended after the defaults
set by runpod_cli, so it can override them (e.g. `HISTFILE`, `UV_LINK_MODE`,
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`).

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


## Retrying when no pods are available

If RunPod reports that no instances match the requested specifications, `rpc`
prints the error without a traceback and exits with code **75**. Other RunPod API
errors exit with code **1**. Exit code 75 means a later attempt may succeed; it
does not guarantee capacity will become available.

This Bash example retries only the no-capacity error, up to ten attempts:

```bash
for attempt in {1..10}; do
    if rpc create --gpu_type=5090; then
        break
    else
        status=$?
    fi
    if [ "$status" -ne 75 ] || [ "$attempt" -eq 10 ]; then
        exit "$status"
    fi
    sleep 30
done
```

## Known issues

Python Fire has a known issue (fixed & merged on [GitHub](https://github.com/google/python-fire/pull/588/files) but not released on PyPI yet)
with ipython>=9.0 which will produce the following error:
```
ERROR  | Uncaught exception | <class 'TypeError'>; Inspector.__init__() missing 1 required keyword-only argument: 'theme_name';
```

## Future features & improvements
- Find a better way to wait for ssh keys to be generated than `time.sleep(5)`
- Allow user to configre an SSH_PUBLIC_KEY_PATH in .env
- Create a .config/runpod_cli/config file to change the default values (e.g. GPU type, runtime, etc.)
