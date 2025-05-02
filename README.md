# RunPod CLI Manager

A command-line tool for managing RunPod instances via the RunPod API.

## Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env_example` to `.env` and add your **RunPod API key**.
4. _(Optional)_ Create a RunPod network-volume.
   The CLI defaults to the public demo volume `t3uq90cdfb`, but you can supply your own at runtime with
   `--network_volume_id=<YOUR_VOLUME_ID>`.
5. Copy `start.sh` and `terminate.sh` to your network volume and make them executable:
   ```bash
   chmod +x start.sh terminate.sh
   ```
   You can do this by deploying a pod from the RunPod web UI with the volume attached.

## Usage

The CLI offers the following high-level commands:

* List pods
* Inspect a pod
* Create a pod (fully parameterised)
* Terminate a pod

### Quick examples

Create a dev pod with one A40 GPU for **1 hour** (using the default network volume):

```bash
python cli.py create_pod \
    --name "mary-pod" \
    --gpu_type "NVIDIA A40" \
    --runtime 60
```

Create the same pod but mount **your own** network volume:

```bash
python cli.py create_pod \
    --name "mary-pod" \
    --gpu_type "NVIDIA A40" \
    --runtime 60 \
    --network_volume_id "<YOUR_VOLUME_ID>"
```

Run a custom script and let the pod self-terminate:

```bash
python cli.py create_pod \
    --name "mary-pod" \
    --gpu_type "NVIDIA A40" \
    --args "python my_script.py"
```

For a full list of flags and defaults, see the docstring at the top of `cli.py`.