# RunPod CLI Manager

A command-line tool for managing RunPod instances using the RunPod API.

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env_example` to `.env` and add your RunPod API key.

## Usage

First, you must create a network volume and copy the `start.sh` and `terminate.sh` files to it.
Then, ensure they are executable (e.g. `chmod +x start.sh` and `chmod +x terminate.sh`).

The CLI provides several commands to manage RunPod instances including:
- Listing all pods
- Getting details for specific pods
- Creating new pods with customizable parameters
- Terminating pods

To create a new pod, use the `create_pod` command. For example:
```
python cli.py create_pod --name="mary-pod" --gpu_type="NVIDIA A40" --network_volume_id="fe90u94tti" --runtime=60
```

For detailed usage instructions and examples, see the docstring in `cli.py`.