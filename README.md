# RunPod CLI Manager

A command-line tool for managing RunPod instances using the RunPod API.

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env_example` to `.env` and add your RunPod API key:
   ```
   cp .env_example .env
   # Edit .env and add your API key
   ```

## Usage

The CLI provides several commands to manage RunPod instances including:
- Listing all pods
- Getting details for specific pods
- Creating new pods with customizable parameters
- Terminating pods
- Generating SSH commands for pods

For detailed usage instructions and examples, see the docstring in `run_pod_deploy.py` or run:

python -c "import run_pod_deploy; help(run_pod_deploy)" 