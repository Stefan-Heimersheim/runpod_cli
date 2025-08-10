import logging
import os
import re
import textwrap
import time
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


def getenv(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"{key} not found in environment. Set it in your .env file.")
    return value


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

        runpod.api_key = getenv("RUNPOD_API_KEY")
        self.network_volume_id: str = getenv("RUNPOD_NETWORK_VOLUME_ID")
        s3_access_key_id = getenv("RUNPOD_S3_ACCESS_KEY_ID")
        s3_secret_key = getenv("RUNPOD_S3_SECRET_KEY")
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
        if not ip or port is None:
            raise ValueError(f"Expected public IP and port, got {ip} and {port} from {public_ips}")
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
            total = int(remaining.total_seconds())
            remaining_str = f"{total // 3600}h {(total % 3600) // 60}m"
            return remaining_str if total > 0 else "Unknown"
        else:
            return "Unknown"

    def _get_gpu_id(self, gpu_type: str) -> Tuple[str, str]:
        if gpu_type in GPU_DISPLAY_NAME_TO_ID:
            # A name was passed
            gpu_id = GPU_DISPLAY_NAME_TO_ID[gpu_type]
            gpu_name = gpu_type
        elif gpu_type in GPU_ID_TO_DISPLAY_NAME:
            # An ID was passed
            gpu_id = gpu_type
            gpu_name = GPU_ID_TO_DISPLAY_NAME[gpu_id]
        else:
            # Attempt fuzzy matching, but only if unique
            matches = [gpu_id for gpu_name, gpu_id in GPU_DISPLAY_NAME_TO_ID.items() if gpu_type.lower() in gpu_id.lower() or gpu_type.lower() in gpu_name.lower()]
            if len(matches) == 1:
                gpu_id = matches[0]
                gpu_name = GPU_ID_TO_DISPLAY_NAME[gpu_id]
            elif len(matches) > 1:
                raise ValueError(f"Ambiguous GPU type: {gpu_type} matches {matches}. Please use a full name or ID from https://docs.runpod.io/references/gpu-types")
            else:
                raise ValueError(f"Unknown GPU type: {gpu_type}")
        return gpu_id, gpu_name

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
        gpu_type: str = "RTX A4000",
        cpus: int = 1,
        disk: int = 30,
        forward_agent: bool = False,
        image_name: str = DEFAULT_IMAGE_NAME,
        memory: int = 1,
        num_gpus: int = 1,
        update_known_hosts: bool = True,
        update_ssh_config: bool = True,
        volume_mount_path: str = "/network",
    ) -> None:
        """Create a new RunPod instance with the specified parameters.

        Args:
            runtime: Time in minutes for pod to run (default: 60)
            gpu_type: GPU type (default: "RTX A4000")
            num_gpus: Number of GPUs (default: 1)
            name: Name for the pod (default: "$USER-$GPU_TYPE")
            env: Path to credentials .env (defalt: .env and ~/.config/runpod_cli/.env)
            disk: Container disk size in GB (default: 30)
            cpus: Minimum CPU count (default: 1)
            memory: Minimum RAM in GB (default: 1)
            forward_agent: Whether to forward SSH agent (default: False)
            update_known_hosts: Whether to update known hosts (default: True)
            update_ssh_config: Whether to update SSH config (default: True)
            image_name: Docker image (default: "PyTorch 2.8.0 with CUDA 12.8.1")

        Example:
            rpc create -r 60 -g "A100 SXM"
            rpc create --gpu_type="RTX A4000" --runtime=480
        """
        gpu_id, gpu_name = self._get_gpu_id(gpu_type)
        name = name or f"{os.getenv('USER')}-{gpu_name}"
        runpodcli_dir = f".tmp_{name.replace(' ', '_')}"

        logging.info("Creating pod with:")
        logging.info(f"  Name: {name}")
        logging.info(f"  Image: {image_name}")
        logging.info(f"  Network volume ID: {self.network_volume_id}")
        logging.info(f"  Region: {self.region}")
        logging.info(f"  S3 endpoint: {self.s3_endpoint}")
        logging.info(f"  GPU Type: {gpu_type}")
        logging.info(f"  GPU Count: {num_gpus}")
        logging.info(f"  Disk: {disk} GB")
        logging.info(f"  Min CPU: {cpus}")
        logging.info(f"  Min Memory: {memory} GB")
        logging.info(f"  runpodcli directory: {runpodcli_dir}")
        logging.info(f"  Time limit: {runtime} minutes")

        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")
        remote_scripts_path = f"{volume_mount_path}/{runpodcli_dir}"
        scripts = [
            get_setup_root(remote_scripts_path, volume_mount_path),
            get_setup_user(remote_scripts_path, git_email, git_name),
            get_start(remote_scripts_path),
            get_terminate(remote_scripts_path),
        ]
        for script_name, script_content in scripts:
            # s3_key is relative to /volume_mount_path, while remote_scripts_path is relative to /
            s3_key = f"{runpodcli_dir}/{script_name}"
            self._s3.put_object(Bucket=self.network_volume_id, Key=s3_key, Body=script_content.encode("utf-8"))

        docker_args = self._build_docker_args(volume_mount_path=volume_mount_path, runpodcli_dir=runpodcli_dir, runtime=runtime)

        pod = runpod.create_pod(
            name=name,
            image_name=image_name,
            gpu_type_id=gpu_id,
            cloud_type="SECURE",
            gpu_count=num_gpus,
            container_disk_in_gb=disk,
            min_vcpu_count=cpus,
            min_memory_in_gb=memory,
            docker_args=docker_args,
            ports="8888/http,22/tcp",
            volume_mount_path=volume_mount_path,
            network_volume_id=self.network_volume_id,
        )

        pod_id: str = pod.get("id")  # type: ignore
        logging.info("Pod created. Provisioning...")
        pod = self._provision_and_wait(pod_id)
        logging.info("Pod provisioned.")

        ip, port = self._get_public_ip_and_port(pod)

        if update_ssh_config:
            self._write_ssh_config(ip, port, forward_agent)

        if update_known_hosts:
            time.sleep(5)
            self._update_known_hosts_file(ip, port, runpodcli_dir)

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
