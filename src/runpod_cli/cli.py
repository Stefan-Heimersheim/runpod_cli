import logging
import os
import re
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    from utils import (  # type: ignore
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
    gpu_type: str = "RTX A4000"
    image_name: str = DEFAULT_IMAGE_NAME
    gpu_count: int = 1
    min_vcpu_count: int = 1
    min_memory_in_gb: int = 1
    container_disk_in_gb: int = 30
    volume_mount_path: str = "/network"
    runpodcli_dir: Optional[str] = None
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


class RunPodManager:
    """RunPod Management CLI - A command-line tool for managing RunPod instances via the RunPod API.

    Available commands:
        create      Create a new pod with specified parameters
        list        List all pods in your account
        terminate   Terminate a specific pod

    Global options:
        --env       Path to the .env file (optional). If not provided, will search for .env files in default locations.

    Examples:
        rpc create --gpu_type="RTX A4000" --runtime=60
        rpc list
        rpc terminate --pod_id=YOUR_POD_ID
        rpc --env=/path/to/custom.env list
    """

    def __init__(self, env: Optional[str] = None) -> None:
        if env:
            logging.info(f"Using .env file: {env}")
            env_path = os.path.expanduser(env)
            if not os.path.exists(env_path):
                raise FileNotFoundError(f"Specified .env file not found: {env_path}")
            load_dotenv(override=True, dotenv_path=env_path)
        else:
            xdg_config_dir = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
            env_paths = [".env", os.path.join(xdg_config_dir, "runpod_cli/.env")]
            env_exists = [os.path.exists(os.path.expanduser(path)) for path in env_paths]
            if not any(env_exists):
                raise FileNotFoundError(f"No .env file found in {env_paths}")
            if env_exists.count(True) > 1:
                raise FileExistsError(f"Multiple .env files found in {env_paths}")
            load_dotenv(override=True, dotenv_path=os.path.expanduser(env_paths[env_exists.index(True)]))

        api_key = os.getenv("RUNPOD_API_KEY")
        if not api_key:
            raise ValueError("RUNPOD_API_KEY not found in environment. Set it in your .env file.")
        runpod.api_key = api_key

        network_volume_id = os.getenv("RUNPOD_NETWORK_VOLUME_ID")
        if not network_volume_id:
            raise ValueError("RUNPOD_NETWORK_VOLUME_ID not found in environment. Set it in your .env file.")
        self.network_volume_id: str = network_volume_id

        s3_access_key_id = os.getenv("RUNPOD_S3_ACCESS_KEY_ID")
        s3_secret_key = os.getenv("RUNPOD_S3_SECRET_KEY")
        if not s3_access_key_id or not s3_secret_key:
            raise ValueError("RUNPOD_S3_ACCESS_KEY_ID or RUNPOD_S3_SECRET_KEY not found in environment. Set it in your .env file.")
        self.region = get_region_from_volume_id(self.network_volume_id)
        self.s3_endpoint = get_s3_endpoint_from_volume_id(self.network_volume_id)
        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=s3_access_key_id,
            aws_secret_access_key=s3_secret_key,
            endpoint_url=self.s3_endpoint,
            region_name=self.region,
        )

    def _build_docker_args(self, volume_mount_path: str, runpodcli_dir: str, runtime: int) -> str:
        runpodcli_path = f"{volume_mount_path}/{runpodcli_dir}"
        return (
            "/bin/bash -c '"
            + (f"mkdir -p {runpodcli_path}; bash {runpodcli_path}/start_pod.sh; sleep {max(runtime * 60, 20)}; bash {runpodcli_path}/terminate_pod.sh")
            + "'"
        )

    def _provision_and_wait(self, pod_id: str, n_attempts: int = 60) -> Dict:
        for _ in range(n_attempts):
            pod = runpod.get_pod(pod_id)
            pod_runtime = pod.get("runtime")
            if pod_runtime is None or not pod_runtime.get("ports"):
                time.sleep(5)
            else:
                return pod
        raise RuntimeError("Pod provisioning failed")

    def _get_public_ip_and_port(self, pod: Dict) -> Tuple[str, int]:
        public_ips = [i for i in pod["runtime"]["ports"] if i["isIpPublic"]]
        if len(public_ips) != 1:
            raise ValueError(f"Expected 1 public IP, got {public_ips}")
        ip = public_ips[0].get("ip")
        port = public_ips[0].get("publicPort")
        return ip, port

    def _parse_time_remaining(self, pod: Dict) -> str:
        _sleep_re = re.compile(r"\bsleep\s+(\d+)\b")
        _date_re = re.compile(r":\s*(\w{3}\s+\w{3}\s+\d{2}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\s+GMT")
        start_dt = None
        sleep_secs = None
        last_status_change = pod.get("lastStatusChange", "")
        if isinstance(last_status_change, str):
            match = _date_re.search(last_status_change)
            if match:
                start_dt = datetime.strptime(match.group(1), "%a %b %d %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        docker_args = pod.get("dockerArgs", "")
        if isinstance(docker_args, str):
            match = _sleep_re.search(docker_args)
            if match:
                sleep_secs = int(match.group(1))
        if start_dt is not None and sleep_secs is not None:
            now_dt = datetime.now(timezone.utc)
            shutdown_dt = start_dt + timedelta(seconds=sleep_secs)
            remaining = shutdown_dt - now_dt
            remaining_str = f"{remaining.seconds // 3600}h {remaining.seconds % 3600 // 60}m"
            return remaining_str if remaining.total_seconds() > 0 else "Unknown"
        else:
            return "Unknown"

    def list(self, verbose: bool = False) -> None:
        """List all pods in your RunPod account.

        Displays information about each pod including ID, name, GPU type, status, and connection details.
        """
        pods = runpod.get_pods()  # type: ignore

        for i, pod in enumerate(pods):
            logging.info(f"Pod {i + 1}:")
            logging.info(f"  ID: {pod.get('id')}")
            logging.info(f"  Name: {pod.get('name')}")
            time_remaining = self._parse_time_remaining(pod)
            logging.info(f"  Time remaining (est.): {time_remaining}")
            if verbose:
                public_ip, public_port = self._get_public_ip_and_port(pod)
                logging.info(f"  Public IP: {public_ip}")
                logging.info(f"  Public port: {public_port}")
                logging.info(f"  GPUs: {pod.get('gpuCount')} x {pod.get('machine', {}).get('gpuDisplayName')}")
                for key in ["memoryInGb", "vcpuCount", "containerDiskInGb", "volumeMountPath", "costPerHr"]:
                    logging.info(f"  {key}: {pod.get(key)}")
            logging.info("")

    def create(
        self,
        name: Optional[str] = None,
        runtime: int = 60,
        gpu_type: Optional[str] = None,
        cpus: Optional[int] = None,
        disk: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        forward_agent: Optional[bool] = None,
        image_name: Optional[str] = None,
        memory: Optional[int] = None,
        num_gpus: Optional[int] = None,
        update_known_hosts: Optional[bool] = None,
        update_ssh_config: Optional[bool] = None,
        volume_mount_path: Optional[str] = None,
    ) -> None:
        """Create a new RunPod instance with the specified parameters.

        Args:
            runtime: Time in minutes for pod to run (default: 60)
            gpu_type: GPU type (default: "RTX A4000")
            num_gpus: Number of GPUs (default: 1)
            name: Name for the pod (default: "$USER-$GPU_TYPE")
            env: File to load RunPod credentials from (default: .env and ~/.config/runpod_cli/.env)
            disk: Container disk size in GB (default: 30)
            cpus: Minimum CPU count (default: 1)
            memory: Minimum RAM in GB (default: 1)
            forward_agent: Whether to forward SSH agent (default: False)
            update_known_hosts: Whether to update known hosts (default: True)
            update_ssh_config: Whether to update SSH config (default: True)
            image_name: Docker image (default: "PyTorch 2.8.0 with CUDA 12.8.1")

        Examples:
            rpc create -r 60 -g "A100 PCIe"
            rpc create --runtime=240 --gpu_type="RTX A4000" --num_gpus=2 --name="dual-gpu-pod"
        """
        spec = PodSpec()
        if gpu_type is not None:
            spec.gpu_type = gpu_type
        if image_name is not None:
            spec.image_name = image_name
        if num_gpus is not None:
            spec.gpu_count = num_gpus
        if cpus is not None:
            spec.min_vcpu_count = cpus
        if memory is not None:
            spec.min_memory_in_gb = memory
        if disk is not None:
            spec.container_disk_in_gb = disk
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

        gpu_id = GPU_DISPLAY_NAME_TO_ID[spec.gpu_type] if spec.gpu_type in GPU_DISPLAY_NAME_TO_ID else spec.gpu_type
        name = name or f"{os.getenv('USER')}-{GPU_ID_TO_DISPLAY_NAME[gpu_id]}"
        spec.runpodcli_dir = spec.runpodcli_dir or f".tmp_{name.replace(' ', '_')}"

        logging.info("Creating pod with:")
        logging.info(f"  Name: {name}")
        logging.info(f"  Image: {spec.image_name}")
        logging.info(f"  Network volume ID: {self.network_volume_id}")
        logging.info(f"  Region: {self.region}")
        logging.info(f"  S3 endpoint: {self.s3_endpoint}")
        logging.info(f"  GPU Type: {spec.gpu_type}")
        logging.info(f"  GPU Count: {spec.gpu_count}")
        logging.info(f"  Disk: {spec.container_disk_in_gb} GB")
        logging.info(f"  Min CPU: {spec.min_vcpu_count}")
        logging.info(f"  Min Memory: {spec.min_memory_in_gb} GB")
        logging.info(f"  runpodcli directory: {spec.runpodcli_dir}")
        logging.info(f"  Time limit: {runtime} minutes")

        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")
        rpc_path = f"{spec.volume_mount_path}/{spec.runpodcli_dir}"
        scripts = [
            get_setup_root(rpc_path, spec.volume_mount_path),
            get_setup_user(rpc_path, git_email, git_name),
            get_start(rpc_path),
            get_terminate(rpc_path),
        ]
        for script_name, script_content in scripts:
            self._s3.put_object(Bucket=self.network_volume_id, Key=f"{spec.runpodcli_dir}/{script_name}", Body=script_content.encode("utf-8"))

        docker_args = self._build_docker_args(volume_mount_path=spec.volume_mount_path, runpodcli_dir=spec.runpodcli_dir, runtime=runtime)

        pod = runpod.create_pod(
            name=name,
            image_name=spec.image_name,
            gpu_type_id=gpu_id,
            cloud_type="SECURE",
            gpu_count=spec.gpu_count,
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
        logging.info("Pod created. Provisioning...")
        pod = self._provision_and_wait(pod_id)
        logging.info("Pod provisioned.")

        ip, port = self._get_public_ip_and_port(pod)

        if spec.update_ssh_config:
            self._write_ssh_config(ip, port, spec.forward_agent)

        if spec.update_known_hosts:
            time.sleep(5)
            if spec.runpodcli_dir is None:
                raise ValueError("runpodcli_dir should be set by this point")
            self._update_known_hosts_file(ip, port, spec.runpodcli_dir)

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

    def _update_known_hosts_file(self, public_ip: str, port: int, runpodcli_dir: str) -> None:
        host_keys: List[Tuple[str, str]] = []
        for file in ["ssh_ed25519_host_key", "ssh_ecdsa_host_key", "ssh_rsa_host_key", "ssh_dsa_host_key"]:
            try:
                obj = self._s3.get_object(Bucket=self.network_volume_id, Key=f"{runpodcli_dir}/{file}")
                host_key_text = obj["Body"].read().decode("utf-8").strip()
                alg, key, _ = host_key_text.split(" ")
                host_keys.append((alg, key))
            except Exception:
                continue

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
        _ = runpod.terminate_pod(pod_id)


def main():
    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
