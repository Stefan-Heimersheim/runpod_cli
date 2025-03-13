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

To create a dev pod with 1 A40 GPU that lasts for 1 hour (as opposed to the default 2 hours):
```
python cli.py create_pod --name="mary-pod" --gpu_type="NVIDIA A40" --runtime=60
```

To run a custom python script `my_script.py` on a pod (and let it terminate after it finishes):
```
python cli.py create_pod --name="mary-pod" --gpu_type="NVIDIA A40" --args="python my_script.py"
```

For detailed usage instructions and examples, see the docstring in `cli.py`.