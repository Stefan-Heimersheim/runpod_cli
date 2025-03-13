# RunPod CLI Manager

A command-line tool for managing RunPod instances using the RunPod API.

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env_example` to `.env` and add your RunPod API key.
4. Create a runpod network volume using the RunPod website and add the ID to your `.env` file.
5. Copy `start.sh` and `terminate.sh` to your network volume and make them executable (e.g. `chmod
   +x start.sh` and `chmod +x terminate.sh`). You can do this by deploying a pod on the website with
   the network volume attached.

## Usage

The CLI provides several commands to manage RunPod instances including:
- Listing all pods
- Getting details for specific pods
- Creating new pods with customizable parameters
- Terminating pods

To create a new pod, use the `create_pod` command. For example:
```
python cli.py create_pod --name="mary-pod" --gpu_type="NVIDIA A40" --runtime=60
```

For detailed usage instructions and examples, see the docstring in `cli.py`.