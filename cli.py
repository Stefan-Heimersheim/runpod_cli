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
    python cli.py create_pod --name="my_project" --gpu_type="NVIDIA A40"

Additional parameters for create_pod (all optional):
    - image_name: Docker image (default: "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04")
    - cloud_type: "SECURE" or "COMMUNITY" (default: "SECURE")
    - gpu_count: Number of GPUs (default: 1)
    - volume_in_gb: Ephemeral storage volume size (default: 0)
    - min_vcpu_count: Minimum CPU count (default: 1)
    - min_memory_in_gb: Minimum RAM in GB (default: 1)
    - docker_args: Arguments passed to Docker (default: "sleep infinity")
    - volume_mount_path: Volume mount path (default: "/ssd")
    - network_volume_id: Network volume ID (default: "fe90u94tti")

Terminate a pod:
    python cli.py terminate_pod --pod_id="YOUR_POD_ID"

"""

import os
from typing import Any

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
            raise ValueError(
                "RUNPOD_API_KEY not found in environment. Please set it in your .env file."
            )

        # Set up RunPod client
        runpod.api_key = self.api_key

        # Dictionary to store pod host IDs
        self.pod_host_ids: dict[str, str] = {}

    def list_pods(self) -> list[dict[str, Any]]:
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
            print()

        return pods

    def get_pod(self, pod_id: str) -> dict[str, Any]:
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
        print(f"  Full Data: {pod}")

        return pod

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
        docker_args: str = "sleep infinity",
        volume_mount_path: str = "/ssd",
        network_volume_id: str = "fe90u94tti",
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
            docker_args: Arguments passed to Docker
            volume_mount_path: Path where volume will be mounted
            network_volume_id: ID of network volume to attach
        """
        print("Creating pod with:")
        print(f"  Name: {name}")
        print(f"  Image: {image_name}")
        print(f"  GPU Type: {gpu_type}")
        print(f"  Cloud Type: {cloud_type}")
        print(f"  GPU Count: {gpu_count}")

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
            volume_mount_path=volume_mount_path,
            network_volume_id=network_volume_id,
        )

        # Store the pod host ID when we create a pod
        pod_host_id = pod.get("machine", {}).get("podHostId")
        if pod_host_id:
            self.pod_host_ids[pod["id"]] = pod_host_id

        print("Pod created:")
        print(f"  Instance ID: {pod.get('id')}")
        print(f"  Pod Host ID: {pod.get('machine', {}).get('podHostId')}")
        print(f"  SSH command: ssh {pod_host_id}@ssh.runpod.io")

    def terminate_pod(self, pod_id: str) -> dict[str, Any]:
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

        return result


def main() -> None:
    """
    Main entry point for the CLI application.
    """
    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
