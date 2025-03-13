"""
RunPod Management CLI

A command-line tool for managing RunPod instances using the RunPod API.
Features include listing, creating, and terminating pods with customizable parameters.

Usage
-----
The CLI provides several commands to manage RunPod instances:

List all pods:
    python cli.py list_pods

Get details for a specific pod:
    python cli.py get_pod --pod_id="YOUR_POD_ID"

Create a new pod:
    python cli.py create_pod --name="pod" --gpu_type="NVIDIA A40" --runtime=60

Parameters for create_pod (all optional):
    - name: Name for the pod
    - image_name: Docker image (default: "ufr308j434f/pytorch-custom:latest")
    - gpu_type: GPU type (default: "NVIDIA A40")
    - cloud_type: "SECURE" or "COMMUNITY" (default: "SECURE")
    - gpu_count: Number of GPUs (default: 1)
    - volume_in_gb: Ephemeral storage volume size (default: 0)
    - min_vcpu_count: Minimum CPU count (default: 1)
    - min_memory_in_gb: Minimum RAM in GB (default: 1)
    - docker_args: Arguments passed to Docker (by default runs ./start.sh and ./terminate.sh with
        sleep in between)
    - volume_mount_path: Volume mount path (default: "/ssd")
    - env: Environment variables to set in the container
    - runtime: Time in minutes for pod to run. Default is 120 minutes.

Terminate a pod:
    python cli.py terminate_pod --pod_id="YOUR_POD_ID"

"""

import os
import time

import fire
import runpod
from dotenv import load_dotenv


class RunPodManager:
    """Manages RunPod operations including creation and termination of pods."""

    def __init__(self) -> None:
        """
        Initialize the RunPod manager.
        """
        # Load environment variables from .env file
        load_dotenv()

        # Get API key from environment
        self.api_key = os.getenv("RUNPOD_API_KEY")
        if not self.api_key:
            raise ValueError("RUNPOD_API_KEY not found in environment. Set it in your .env file.")

        self.network_volume_id = os.getenv("RUNPOD_NETWORK_VOLUME_ID")
        if not self.network_volume_id:
            raise ValueError(
                "RUNPOD_NETWORK_VOLUME_ID not found in environment. Set it in your .env file."
            )

        # Set up RunPod client
        runpod.api_key = self.api_key

        # Dictionary to store pod host IDs
        self.pod_host_ids: dict[str, str] = {}

    def list_pods(self) -> None:
        """
        List all pods in the account.

        Returns:
            List of pod dictionaries
        """
        pods = runpod.get_pods()

        for i, pod in enumerate(pods):
            print(f"Pod {i + 1}:")
            print(f"  ID: {pod.get('id')}")
            print(f"  Name: {pod.get('name')}")
            print(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
            print(f"  Status: {pod.get('desiredStatus')}")
            public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
            assert len(public_ip) == 1
            print(f"  Public IP: {public_ip[0].get('ip')}")
            print(f"  Public port: {public_ip[0].get('publicPort')}")
            print()

    def get_pod(self, pod_id: str) -> None:
        """
        Get detailed information about a specific pod.

        Args:
            pod_id: The ID of the pod to retrieve

        Returns:
            Pod information dictionary
        """
        pod = runpod.get_pod(pod_id)

        print("Pod Details:")
        print(f"  ID: {pod.get('id')}")
        print(f"  Name: {pod.get('name')}")
        print(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
        print(f"  Status: {pod.get('desiredStatus')}")
        print(f"  Pod Host ID: {pod.get('machine', {}).get('podHostId')}")
        public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
        assert len(public_ip) == 1
        print(f"  Public IP: {public_ip[0].get('ip')}")
        print(f"  Public port: {public_ip[0].get('publicPort')}")
        print(f"  Full Data: {pod}")

    def create_pod(
        self,
        name: str = "test",
        image_name: str = "ufr308j434f/pytorch-custom:latest",
        gpu_type: str = "NVIDIA A40",
        cloud_type: str = "SECURE",
        gpu_count: int = 1,
        volume_in_gb: int = 0,
        min_vcpu_count: int = 1,
        min_memory_in_gb: int = 1,
        docker_args: str = "",
        volume_mount_path: str = "/ssd",
        env: dict[str, str] | None = None,
        runtime: int = 120,
    ) -> None:
        """
        Create a new pod with the specified parameters.

        Args:
            name: Name for the pod
            image_name: Docker image to use
            gpu_type: Type of GPU to request (e.g., "NVIDIA A40")
            cloud_type: Cloud type ("SECURE" or "COMMUNITY")
            gpu_count: Number of GPUs to allocate
            volume_in_gb: Size of ephemeral storage volume in GB
            min_vcpu_count: Minimum vCPU count
            min_memory_in_gb: Minimum RAM in GB
            docker_args: Appends to the default command of "/bin/bash /ssd/start.sh" to start the
                container.
            volume_mount_path: Path where volume will be mounted
            env: Environment variables to set in the container
            runtime: Time in minutes for pod to run. Default is 120 minutes.
        """
        print("Creating pod with:")
        print(f"  Name: {name}")
        print(f"  Image: {image_name}")
        print(f"  GPU Type: {gpu_type}")
        print(f"  Cloud Type: {cloud_type}")
        print(f"  GPU Count: {gpu_count}")
        print(f"  Time limit: {runtime} minutes")

        # NOTE: Must use this structure in order to work (i.e. with -c and ; separated commands)
        docker_args = f"/bin/bash -c '/ssd/start.sh; sleep {runtime * 60}; /ssd/terminate.sh'"

        pod = runpod.create_pod(
            name=name,
            image_name=image_name,
            gpu_type_id=gpu_type,
            cloud_type=cloud_type,
            gpu_count=gpu_count,
            volume_in_gb=volume_in_gb,
            min_vcpu_count=min_vcpu_count,
            min_memory_in_gb=min_memory_in_gb,
            docker_args=docker_args,
            env=env,
            ports="8888/http,22/tcp",
            volume_mount_path=volume_mount_path,
            network_volume_id=self.network_volume_id,
        )
        pod_id = pod.get("id")
        # Store the pod host ID when we create a pod
        pod_host_id = pod.get("machine", {}).get("podHostId")
        if pod_host_id:
            self.pod_host_ids[pod_id] = pod_host_id

        print("Pod created:")
        print(f"  Instance ID: {pod_id}")
        print(f"  Pod Host ID: {pod_host_id}")
        # Run a loop, printing every 5 seconds until public_ip > 0
        while True:
            pod = runpod.get_pod(pod_id)
            if (
                pod["runtime"] is None
                or "ports" not in pod["runtime"]
                or pod["runtime"]["ports"] is None
            ):
                print("  Provisioning...")
                time.sleep(5)
            else:
                break
        public_ip = [i for i in pod["runtime"]["ports"] if i["isIpPublic"]]
        assert len(public_ip) == 1, f"Expected 1 public IP, got {len(public_ip)}"
        ip = public_ip[0].get("ip")
        port = public_ip[0].get("publicPort")
        print(f"  Public IP: {ip}")
        print(f"  Public port: {port}")
        print(f"  basic SSH command:\nssh {pod_host_id}@ssh.runpod.io")
        print(f"  full SSH command ('user' depends on the docker image):\nssh user@{ip} -p {port}")

    def terminate_pod(self, pod_id: str) -> None:
        """
        Terminate a specific pod.

        Args:
            pod_id: ID of the pod to terminate

        Returns:
            Termination result dictionary
        """
        print(f"Terminating pod {pod_id}...")
        result = runpod.terminate_pod(pod_id)
        print(f"Pod terminated: {result}")


def main() -> None:
    """
    Main entry point for the CLI application.
    """
    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
