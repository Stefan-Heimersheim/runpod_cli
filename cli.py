"""
RunPod Management CLI

A command-line tool for managing RunPod instances using the RunPod API.
Features include listing, creating, and terminating pods with customizable parameters.

Usage
-----
The CLI provides several commands to manage RunPod instances:

Create a dev pod with 1 RTX A4000 GPU that lasts for 1 hour:
    python cli.py create_pod --gpu_type="RTX A4000" --runtime=60

Terminate a pod:
    python cli.py terminate_pod --pod_id="YOUR_POD_ID"

List all pods:
    python cli.py list_pods

Get details for a specific pod:
    python cli.py get_pod --pod_id="YOUR_POD_ID"

Available Commands:
    create_pod      Create a new pod with specified parameters
    list_pods       List all pods in your account
    get_pod         Get detailed information about a specific pod
    terminate_pod   Terminate a specific pod

Create Pod Parameters:
    - name: Name for the pod (default: "$USER-$GPU_TYPE")
    - runtime: Time in minutes for pod to run (default: 120)
    - gpu_type: GPU type (default: "RTX A4000")
    - image_name: Docker image (default: "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04")
    - cloud_type: "SECURE" or "COMMUNITY" (default: "SECURE")
    - gpu_count: Number of GPUs (default: 1)
    - volume_in_gb: Ephemeral storage volume size (default: 10)
    - min_vcpu_count: Minimum CPU count (default: 1)
    - min_memory_in_gb: Minimum RAM in GB (default: 1)
    - container_disk_in_gb: Container disk size (default: 30)
    - volume_mount_path: Volume mount path (default: "/network")
    - env: Environment variables to set in the container
    - update_ssh_config: Whether to update SSH config (default: True)
    - forward_agent: Whether to forward SSH agent (default: False)
    - update_known_hosts: Whether to update known hosts (default: True)
"""

import logging
import os
import textwrap
import time
from dataclasses import dataclass
from typing import Optional

import boto3
import fire
import requests
from dotenv import load_dotenv

from utils import (
    DEFAULT_IMAGE_NAME,
    GPU_DISPLAY_NAME_TO_ID,
    GPU_ID_TO_DISPLAY_NAME,
    get_setup_root,
    get_setup_user,
    get_start,
    get_terminate,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(asctime)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

import runpod  # noqa: E402


@dataclass
class PodSpec:
    """Specification for RunPod infrastructure parameters."""

    image_name: str = DEFAULT_IMAGE_NAME
    gpu_type: str = "RTX A4000"
    cloud_type: str = "SECURE"
    gpu_count: int = 1
    volume_in_gb: int = 10
    min_vcpu_count: int = 1
    min_memory_in_gb: int = 1
    container_disk_in_gb: int = 30
    volume_mount_path: str = "/network"
    env: Optional[dict[str, str]] = None
    update_ssh_config: bool = True
    forward_agent: bool = False
    update_known_hosts: bool = True


def get_region_from_volume_id(volume_id: str) -> str:
    api_key = os.getenv("RUNPOD_API_KEY")
    url = f"https://rest.runpod.io/v1/networkvolumes/{volume_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise ValueError(f"Failed to get volume info: {response.text}")
    volume_info = response.json()
    return volume_info.get("dataCenterId")


def get_s3_endpoint_from_volume_id(volume_id: str) -> str:
    data_center_id = get_region_from_volume_id(volume_id)
    s3_endpoint = f"https://s3api-{data_center_id.lower()}.runpod.io/"
    return s3_endpoint


class RunPodClient:
    """Pure logic client for RunPod and S3 operations without user I/O."""

    def __init__(self) -> None:
        """Initialize the RunPod client."""
        # Get API key from environment
        self.api_key = os.getenv("RUNPOD_API_KEY")
        if not self.api_key:
            raise ValueError("RUNPOD_API_KEY not found in environment. Set it in your .env file.")

        network_volume_id = os.getenv("RUNPOD_NETWORK_VOLUME_ID")
        if not network_volume_id:
            raise ValueError("RUNPOD_NETWORK_VOLUME_ID not found in environment. Set it in your .env file.")
        self.network_volume_id: str = network_volume_id

        # S3
        self.s3_access_key_id = os.getenv("RUNPOD_S3_ACCESS_KEY_ID")
        self.s3_secret_key = os.getenv("RUNPOD_S3_SECRET_KEY")
        if not self.s3_access_key_id or not self.s3_secret_key:
            raise ValueError("RUNPOD_S3_ACCESS_KEY_ID or RUNPOD_S3_SECRET_KEY not found in environment. Set it in your .env file.")

        # Set up RunPod client
        runpod.api_key = self.api_key

        # Initialize region and S3 endpoint
        self.region = get_region_from_volume_id(self.network_volume_id)
        self.s3_endpoint = get_s3_endpoint_from_volume_id(self.network_volume_id)

    def _make_s3_client(self):
        """Create and return an S3 client with the configured credentials."""
        return boto3.client(
            "s3",
            aws_access_key_id=self.s3_access_key_id,
            aws_secret_access_key=self.s3_secret_key,
            endpoint_url=self.s3_endpoint,
            region_name=self.region,
        )

    def get_pods(self) -> list[dict]:
        """Get all pods from RunPod."""
        return runpod.get_pods()  # type: ignore

    def get_pod(self, pod_id: str) -> dict:
        """Get a specific pod by ID."""
        return runpod.get_pod(pod_id)

    def terminate_pod(self, pod_id: str) -> dict | None:
        """Terminate a specific pod."""
        return runpod.terminate_pod(pod_id)

    def upload_script_content(self, script_name: str, content: str, target_dir: str) -> None:
        """Upload script content directly to S3."""
        s3 = self._make_s3_client()
        s3.put_object(Bucket=self.network_volume_id, Key=f"{target_dir}/{script_name}", Body=content.encode("utf-8"))

    def download_file_text(self, remote_path: str) -> str:
        """Download a text object from S3 and return its contents as a string."""
        s3 = self._make_s3_client()
        response = s3.get_object(Bucket=self.network_volume_id, Key=remote_path)
        data_bytes: bytes = response["Body"].read()
        text: str = data_bytes.decode("utf-8")
        return text

    def _build_docker_args(self, runtime: int, volume_mount_path: str) -> str:
        """Build the Docker arguments string for pod execution."""
        runpodcli_path = f"{volume_mount_path}/runpodcli"
        return "/bin/bash -c '" + (f"mkdir -p {runpodcli_path}; bash {runpodcli_path}/start.sh; sleep {max(runtime * 60, 20)}; bash {runpodcli_path}/terminate.sh") + "'"

    def _provision_and_wait(self, pod_id: str) -> dict:
        """Wait for pod provisioning to complete and return pod info."""
        n_attempts = 120
        i = 1
        while True:
            pod = runpod.get_pod(pod_id)
            pod_runtime = pod.get("runtime")
            if pod_runtime is None or not pod_runtime.get("ports"):
                if i > n_attempts:
                    raise RuntimeError("Pod provisioning failed")
                i += 1
                time.sleep(5)
            else:
                break
        return pod

    def create_pod(self, name: str | None, spec: PodSpec, runtime: int) -> dict:
        """Create a new pod with the specified parameters."""
        # Convert display name to GPU ID if needed
        gpu_id = GPU_DISPLAY_NAME_TO_ID[spec.gpu_type] if spec.gpu_type in GPU_DISPLAY_NAME_TO_ID else spec.gpu_type

        # Get GitHub email and name from environment
        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")

        # Upload required scripts from get_shell_scripts.py
        rpc_path = f"{spec.volume_mount_path}/runpodcli"
        scripts = [
            get_setup_root(rpc_path, spec.volume_mount_path),
            get_setup_user(rpc_path, git_email, git_name),
            get_start(rpc_path),
            get_terminate(rpc_path),
        ]
        for script_name, script_content in scripts:
            self.upload_script_content(script_name=script_name, content=script_content, target_dir="runpodcli")

        # Build Docker arguments
        docker_args = self._build_docker_args(runtime, spec.volume_mount_path)

        # Set name if not provided
        name = name or f"{os.getenv('USER')}-{GPU_ID_TO_DISPLAY_NAME[gpu_id]}"

        pod = runpod.create_pod(
            name=name,
            image_name=spec.image_name,
            gpu_type_id=gpu_id,
            cloud_type=spec.cloud_type,
            gpu_count=spec.gpu_count,
            volume_in_gb=spec.volume_in_gb,
            container_disk_in_gb=spec.container_disk_in_gb,
            min_vcpu_count=spec.min_vcpu_count,
            min_memory_in_gb=spec.min_memory_in_gb,
            docker_args=docker_args,
            env=spec.env,
            ports="8888/http,22/tcp",
            volume_mount_path=spec.volume_mount_path,
            network_volume_id=self.network_volume_id,
        )

        pod_id: str = pod.get("id")  # type: ignore
        pod = self._provision_and_wait(pod_id)

        return pod

    def get_pod_public_ip_and_port(self, pod: dict) -> tuple[str, int]:
        """Extract public IP and port from pod runtime info."""
        public_ip = [i for i in pod["runtime"]["ports"] if i["isIpPublic"]]
        if len(public_ip) != 1:
            raise ValueError(f"Expected 1 public IP, got {len(public_ip)}")
        ip = public_ip[0].get("ip")
        port = public_ip[0].get("publicPort")
        return ip, port

    def get_host_keys(self, remote_path: str) -> list[tuple[str, str]]:
        """Download host keys from S3 and return list of (algorithm, key) pairs."""
        host_keys = []
        for file in ["ssh_ed25519_host_key", "ssh_ecdsa_host_key", "ssh_rsa_host_key", "ssh_dsa_host_key"]:
            try:
                host_key_text = self.download_file_text(f"{remote_path}/{file}").strip()
                alg, key, _ = host_key_text.split(" ")
                host_keys.append((alg, key))
            except Exception:
                continue
        return host_keys


class RunPodManager:
    """CLI wrapper for RunPod operations with user I/O handling."""

    def __init__(self) -> None:
        """Initialize the RunPod manager."""
        self.client = RunPodClient()

    def list_pods(self) -> None:
        """
        List all pods in the account.
        """
        pods = self.client.get_pods()

        for i, pod in enumerate(pods):
            logging.info(f"Pod {i + 1}:")
            logging.info(f"  ID: {pod.get('id')}")
            logging.info(f"  Name: {pod.get('name')}")
            logging.info(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
            logging.info(f"  Status: {pod.get('desiredStatus')}")
            public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
            if len(public_ip) != 1:
                raise ValueError(f"Expected 1 public IP, got {len(public_ip)}")
            logging.info(f"  Public IP: {public_ip[0].get('ip')}")
            logging.info(f"  Public port: {public_ip[0].get('publicPort')}")
            logging.info("")

    def get_pod(self, pod_id: str) -> None:
        """
        Get detailed information about a specific pod.

        Args:
            pod_id: The ID of the pod to retrieve
        """
        pod = self.client.get_pod(pod_id)

        logging.info("Pod Details:")
        logging.info(f"  ID: {pod.get('id')}")
        logging.info(f"  Name: {pod.get('name')}")
        logging.info(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
        logging.info(f"  Status: {pod.get('desiredStatus')}")
        logging.info(f"  Pod Host ID: {pod.get('machine', {}).get('podHostId')}")
        public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
        if len(public_ip) != 1:
            raise ValueError(f"Expected 1 public IP, got {len(public_ip)}")
        logging.info(f"  Public IP: {public_ip[0].get('ip')}")
        logging.info(f"  Public port: {public_ip[0].get('publicPort')}")
        logging.info(f"  Full Data: {pod}")

    def generate_ssh_config(self, ip: str, port: int, forward_agent: bool = False) -> str:
        """
        Generate SSH config for the given IP and port.

        Args:
            ip: IP address of the pod
            port: Port number of the pod
            forward_agent: Whether to forward the agent
        Returns:
            SSH config string
        """
        return textwrap.dedent(f"""
            Host runpod
              HostName {ip}
              User user
              Port {port}
              {"ForwardAgent yes" if forward_agent else ""}
        """).strip()

    def create_pod(
        self,
        name: str | None = None,
        runtime: int = 120,
        spec: Optional[PodSpec] = None,
        gpu_type: str | None = None,
        image_name: str | None = None,
        cloud_type: str | None = None,
        gpu_count: int | None = None,
        volume_in_gb: int | None = None,
        min_vcpu_count: int | None = None,
        min_memory_in_gb: int | None = None,
        container_disk_in_gb: int | None = None,
        volume_mount_path: str | None = None,
        env: Optional[dict[str, str]] = None,
        update_ssh_config: bool | None = None,
        forward_agent: bool | None = None,
        update_known_hosts: bool | None = None,
    ) -> None:
        """
        Create a new pod with the specified parameters.

        Args:
            name: Name for the pod
            runtime: Time in minutes for pod to run. Default is 120 minutes.
            spec: Pod specification with infrastructure parameters
            gpu_type: GPU type (e.g., "A100 PCIe", "RTX A4000")
            image_name: Docker image name
            cloud_type: "SECURE" or "COMMUNITY"
            gpu_count: Number of GPUs
            volume_in_gb: Ephemeral storage volume size in GB
            min_vcpu_count: Minimum CPU count
            min_memory_in_gb: Minimum RAM in GB
            container_disk_in_gb: Container disk size in GB
            volume_mount_path: Volume mount path
            env: Environment variables to set in the container
            update_ssh_config: Whether to update SSH config
            forward_agent: Whether to forward SSH agent
            update_known_hosts: Whether to update known hosts
        """
        if spec is None:
            spec = PodSpec()

        # Override spec with individual parameters if provided
        if gpu_type is not None:
            spec.gpu_type = gpu_type
        if image_name is not None:
            spec.image_name = image_name
        if cloud_type is not None:
            spec.cloud_type = cloud_type
        if gpu_count is not None:
            spec.gpu_count = gpu_count
        if volume_in_gb is not None:
            spec.volume_in_gb = volume_in_gb
        if min_vcpu_count is not None:
            spec.min_vcpu_count = min_vcpu_count
        if min_memory_in_gb is not None:
            spec.min_memory_in_gb = min_memory_in_gb
        if container_disk_in_gb is not None:
            spec.container_disk_in_gb = container_disk_in_gb
        if volume_mount_path is not None:
            spec.volume_mount_path = volume_mount_path
        if env is not None:
            spec.env = env
        if update_ssh_config is not None:
            spec.update_ssh_config = update_ssh_config
        if forward_agent is not None:
            spec.forward_agent = forward_agent
        if update_known_hosts is not None:
            spec.update_known_hosts = update_known_hosts

        logging.info("Creating pod with:")
        logging.info(f"  Name: {name}")
        logging.info(f"  Image: {spec.image_name}")
        logging.info(f"  Network volume ID: {self.client.network_volume_id}")
        logging.info(f"  Region: {self.client.region}")
        logging.info(f"  S3 endpoint: {self.client.s3_endpoint}")
        logging.info(f"  GPU Type: {spec.gpu_type}")
        logging.info(f"  Cloud Type: {spec.cloud_type}")
        logging.info(f"  GPU Count: {spec.gpu_count}")
        logging.info(f"  Time limit: {runtime} minutes")

        logging.info("Pod created. Provisioning...")
        pod = self.client.create_pod(name, spec, runtime)
        logging.info("Pod provisioned.")

        # Handle SSH config and known hosts
        ip, port = self.client.get_pod_public_ip_and_port(pod)

        if spec.update_ssh_config:
            self._write_ssh_config(ip, port, spec.forward_agent)

        if spec.update_known_hosts:
            time.sleep(5)
            self._update_known_hosts_file(ip, port)

    def _write_ssh_config(self, ip: str, port: int, forward_agent: bool) -> None:
        """Write SSH configuration to file."""
        runpod_config = self.generate_ssh_config(ip=ip, port=port, forward_agent=forward_agent)
        with open(os.path.expanduser("~/.ssh/runpod_config"), "w") as f:
            f.write(runpod_config)
        logging.info("SSH config updated")

    def _update_known_hosts_file(self, public_ip: str, port: int) -> None:
        """Update SSH known hosts file with pod host keys."""
        host_keys = self.client.get_host_keys("runpodcli")
        known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")

        for alg, key in host_keys:
            try:
                with open(known_hosts_path, "a") as dest:
                    dest.write(f"# runpod cli:\n[{public_ip}]:{port} {alg} {key}\n")
                logging.info(f"Added host key to {known_hosts_path}")
            except Exception as e:
                logging.error(f"Error adding host key: {e}")

    def terminate_pod(self, pod_id: str) -> None:
        """
        Terminate a specific pod.

        Args:
            pod_id: ID of the pod to terminate
        """
        logging.info(f"Terminating pod {pod_id}...")
        result = self.client.terminate_pod(pod_id)
        logging.info(f"Pod terminated: {result}")


def main() -> None:
    """
    Main entry point for the CLI application.
    """
    # Load environment variables once at startup
    load_dotenv(override=True)
    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
