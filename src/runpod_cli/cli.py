import logging
import os
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import boto3
import fire
import requests
from dotenv import load_dotenv

try:
    from .utils import (
        DEFAULT_IMAGE_NAME,
        GPU_DISPLAY_NAME_TO_ID,
        GPU_ID_TO_DISPLAY_NAME,
        get_setup_root,
        get_setup_user,
        get_start,
        get_terminate,
    )
except ImportError:
    # Allow running cli.py directly from the repository
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
    env: Optional[Dict[str, str]] = None
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
        self.api_key = os.getenv("RUNPOD_API_KEY")
        if not self.api_key:
            raise ValueError("RUNPOD_API_KEY not found in environment. Set it in your .env file.")

        network_volume_id = os.getenv("RUNPOD_NETWORK_VOLUME_ID")
        if not network_volume_id:
            raise ValueError("RUNPOD_NETWORK_VOLUME_ID not found in environment. Set it in your .env file.")
        self.network_volume_id: str = network_volume_id

        self.s3_access_key_id = os.getenv("RUNPOD_S3_ACCESS_KEY_ID")
        self.s3_secret_key = os.getenv("RUNPOD_S3_SECRET_KEY")
        if not self.s3_access_key_id or not self.s3_secret_key:
            raise ValueError("RUNPOD_S3_ACCESS_KEY_ID or RUNPOD_S3_SECRET_KEY not found in environment. Set it in your .env file.")

        runpod.api_key = self.api_key
        self.region = get_region_from_volume_id(self.network_volume_id)
        self.s3_endpoint = get_s3_endpoint_from_volume_id(self.network_volume_id)

    def _make_s3_client(self):
        return boto3.client(
            "s3",
            aws_access_key_id=self.s3_access_key_id,
            aws_secret_access_key=self.s3_secret_key,
            endpoint_url=self.s3_endpoint,
            region_name=self.region,
        )

    def get_pods(self) -> List[Dict]:
        return runpod.get_pods()  # type: ignore

    def get_pod(self, pod_id: str) -> Dict:
        return runpod.get_pod(pod_id)

    def terminate_pod(self, pod_id: str) -> Optional[Dict]:
        return runpod.terminate_pod(pod_id)

    def upload_script_content(self, content: str, target: str) -> None:
        s3 = self._make_s3_client()
        s3.put_object(Bucket=self.network_volume_id, Key=target, Body=content.encode("utf-8"))

    def download_file_content(self, remote_path: str) -> str:
        s3 = self._make_s3_client()
        response = s3.get_object(Bucket=self.network_volume_id, Key=remote_path)
        return response["Body"].read().decode("utf-8")

    def _build_docker_args(self, volume_mount_path: str, runtime: int) -> str:
        runpodcli_path = f"{volume_mount_path}/runpodcli"
        return "/bin/bash -c '" + (f"mkdir -p {runpodcli_path}; bash {runpodcli_path}/start.sh; sleep {max(runtime * 60, 20)}; bash {runpodcli_path}/terminate.sh") + "'"

    def _provision_and_wait(self, pod_id: str, n_attempts: int = 60) -> Dict:
        for i in range(n_attempts):
            pod = runpod.get_pod(pod_id)
            pod_runtime = pod.get("runtime")
            if pod_runtime is None or not pod_runtime.get("ports"):
                time.sleep(5)
            else:
                return pod
        raise RuntimeError("Pod provisioning failed")

    def create_pod(self, name: Optional[str], spec: PodSpec, runtime: int) -> Dict:
        gpu_id = GPU_DISPLAY_NAME_TO_ID[spec.gpu_type] if spec.gpu_type in GPU_DISPLAY_NAME_TO_ID else spec.gpu_type

        # Get scripts and upload to network volume
        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")
        rpc_path = f"{spec.volume_mount_path}/runpodcli"
        scripts = [
            get_setup_root(rpc_path, spec.volume_mount_path),
            get_setup_user(rpc_path, git_email, git_name),
            get_start(rpc_path),
            get_terminate(rpc_path),
        ]
        for script_name, script_content in scripts:
            self.upload_script_content(content=script_content, target=f"runpodcli/{script_name}")

        docker_args = self._build_docker_args(volume_mount_path=spec.volume_mount_path, runtime=runtime)
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

    def get_pod_public_ip_and_port(self, pod: Dict) -> Tuple[str, int]:
        """Extract public IP and port from pod runtime info."""
        public_ips = [i for i in pod["runtime"]["ports"] if i["isIpPublic"]]
        if len(public_ips) != 1:
            raise ValueError(f"Expected 1 public IP, got {public_ips}")
        ip = public_ips[0].get("ip")
        port = public_ips[0].get("publicPort")
        return ip, port

    def get_host_keys(self, remote_path: str) -> List[Tuple[str, str]]:
        """Download host keys from S3 and return list of (algorithm, key) pairs."""
        host_keys = []
        for file in ["ssh_ed25519_host_key", "ssh_ecdsa_host_key", "ssh_rsa_host_key", "ssh_dsa_host_key"]:
            try:
                host_key_text = self.download_file_content(f"{remote_path}/{file}").strip()
                alg, key, _ = host_key_text.split(" ")
                host_keys.append((alg, key))
            except Exception:
                continue
        return host_keys


class RunPodManager:
    """RunPod Management CLI - A command-line tool for managing RunPod instances via the RunPod API.

    Available commands:
        create      Create a new pod with specified parameters
        list        List all pods in your account
        terminate   Terminate a specific pod

    Examples:
        rpc create --gpu_type="RTX A4000" --runtime=60
        rpc list
        rpc terminate --pod_id=YOUR_POD_ID
    """

    def __init__(self) -> None:
        """Initialize the RunPod manager."""
        self._client = RunPodClient()

    def list(self) -> None:
        """List all pods in your RunPod account.

        Displays information about each pod including ID, name, GPU type, status, and connection details.
        """
        pods = self._client.get_pods()

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

    def create(
        self,
        name: Optional[str] = None,
        runtime: int = 60,
        spec: Optional[PodSpec] = None,
        gpu_type: Optional[str] = None,
        image_name: Optional[str] = None,
        cloud_type: Optional[str] = None,
        gpu_count: Optional[int] = None,
        volume_in_gb: Optional[int] = None,
        min_vcpu_count: Optional[int] = None,
        min_memory_in_gb: Optional[int] = None,
        container_disk_in_gb: Optional[int] = None,
        volume_mount_path: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        update_ssh_config: Optional[bool] = None,
        forward_agent: Optional[bool] = None,
        update_known_hosts: Optional[bool] = None,
    ) -> None:
        """Create a new RunPod instance with the specified parameters.

        Args:
            name: Name for the pod (default: "$USER-$GPU_TYPE")
            runtime: Time in minutes for pod to run (default: 60)
            gpu_type: GPU type (default: "RTX A4000")
            image_name: Docker image (default: PyTorch 2.8.0 with CUDA 12.8.1)
            cloud_type: "SECURE" or "COMMUNITY" (default: "SECURE")
            gpu_count: Number of GPUs (default: 1)
            volume_in_gb: Ephemeral storage volume size in GB (default: 10)
            min_vcpu_count: Minimum CPU count (default: 1)
            min_memory_in_gb: Minimum RAM in GB (default: 1)
            container_disk_in_gb: Container disk size in GB (default: 30)
            volume_mount_path: Volume mount path (default: "/network")
            env: Environment variables to set in the container
            update_ssh_config: Whether to update SSH config (default: True)
            forward_agent: Whether to forward SSH agent (default: False)
            update_known_hosts: Whether to update known hosts (default: True)

        Examples:
            rpc create --gpu_type="A100 PCIe" --runtime=60
            rpc create --name="my-pod" --gpu_count=2 --runtime=240
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
        logging.info(f"  Network volume ID: {self._client.network_volume_id}")
        logging.info(f"  Region: {self._client.region}")
        logging.info(f"  S3 endpoint: {self._client.s3_endpoint}")
        logging.info(f"  GPU Type: {spec.gpu_type}")
        logging.info(f"  Cloud Type: {spec.cloud_type}")
        logging.info(f"  GPU Count: {spec.gpu_count}")
        logging.info(f"  Time limit: {runtime} minutes")

        logging.info("Pod created. Provisioning...")
        pod = self._client.create_pod(name, spec, runtime)
        logging.info("Pod provisioned.")

        # Handle SSH config and known hosts
        ip, port = self._client.get_pod_public_ip_and_port(pod)

        if spec.update_ssh_config:
            self._write_ssh_config(ip, port, spec.forward_agent)

        if spec.update_known_hosts:
            time.sleep(5)
            self._update_known_hosts_file(ip, port)

    def _generate_ssh_config(self, ip: str, port: int, forward_agent: bool = False) -> str:
        return textwrap.dedent(f"""
            Host runpod
              HostName {ip}
              User user
              Port {port}
              UserKnownHostsFile ~/.ssh/known_hosts ~/.ssh/known_hosts.runpod_cli
              {"ForwardAgent yes" if forward_agent else ""}
        """).strip()

    def _write_ssh_config(self, ip: str, port: int, forward_agent: bool, config_path: str = "~/.ssh/config.runpod_cli") -> None:
        runpod_config = self._generate_ssh_config(ip=ip, port=port, forward_agent=forward_agent)
        with open(os.path.expanduser(config_path), "w") as f:
            f.write(runpod_config)
        logging.info(f"SSH config at {config_path} updated")

    def _update_known_hosts_file(self, public_ip: str, port: int) -> None:
        """Update SSH known hosts file with pod host keys."""
        host_keys = self._client.get_host_keys("runpodcli")
        known_hosts_path = os.path.expanduser("~/.ssh/known_hosts.runpod_cli")

        for alg, key in host_keys:
            try:
                with open(known_hosts_path, "a") as dest:
                    dest.write(f"# runpod cli:\n[{public_ip}]:{port} {alg} {key}\n")
                logging.info(f"Added {alg} host key to {known_hosts_path}")
            except Exception as e:
                logging.error(f"Error adding host key: {e}")

    def terminate(self, pod_id: str) -> None:
        """Terminate a specific RunPod instance.

        Args:
            pod_id: ID of the pod to terminate

        Example:
            rpc terminate --pod_id=abc123
        """
        logging.info(f"Terminating pod {pod_id}")
        self._client.terminate_pod(pod_id)


def main() -> None:
    """
    Main entry point for the CLI application.
    """
    # Load environment variables once at startup
    xdg_config_dir = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    env_paths = [".env", os.path.join(xdg_config_dir, "runpod_cli/.env")]
    env_exists = [os.path.exists(os.path.expanduser(path)) for path in env_paths]
    if not any(env_exists):
        raise FileNotFoundError(f"No .env file found in {env_paths}")
    if env_exists.count(True) > 1:
        raise FileExistsError(f"Multiple .env files found in {env_paths}")
    load_dotenv(override=True, dotenv_path=os.path.expanduser(env_paths[env_exists.index(True)]))

    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
