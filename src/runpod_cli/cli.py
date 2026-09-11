import glob
import logging
import os
import re
import textwrap
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import boto3
import fire
from dotenv import load_dotenv

try:
    from .api import RunPodAPIError, RunPodGraphQL
    from .utils import (
        DEFAULT_IMAGE_NAME,
        get_setup_root,
        get_setup_user,
        get_start,
        get_terminate,
    )
except ImportError:
    from api import RunPodAPIError, RunPodGraphQL  # type: ignore
    from utils import (  # type: ignore
        DEFAULT_IMAGE_NAME,
        get_setup_root,
        get_setup_user,
        get_start,
        get_terminate,
    )

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(asctime)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")



def getenv(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"{key} not found in environment. Set it in your .env file.")
    return value


def env_default(flag_value, env_var: str, fallback):
    """Resolve a create() parameter: CLI flag > RPC_DEFAULT_* from .env > built-in default."""
    if flag_value is not None:
        return flag_value
    value = os.getenv(env_var)
    if not value:
        return fallback
    logging.info(f"Using {env_var}={value} from .env")
    if isinstance(fallback, bool):
        if value.strip().lower() in ("1", "true", "yes", "on"):
            return True
        if value.strip().lower() in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{env_var} must be a boolean (true/false), got: {value}")
    if isinstance(fallback, int):
        return int(value)
    return value


class RunPodManager:
    """RunPod Management CLI - A command-line tool for managing RunPod instances via the RunPod API.

    Available commands:
        create      Create a new pod with specified parameters
        list        List all pods in your account
        terminate   Terminate a specific pod
        reset       Delete the SSH config files written by runpod_cli

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

        self._api = RunPodGraphQL(getenv("RUNPOD_API_KEY"), team_id=os.getenv("RUNPOD_TEAM_ID"))
        self._network_volume_id: str = getenv("RUNPOD_NETWORK_VOLUME_ID")
        s3_access_key_id = getenv("RUNPOD_S3_ACCESS_KEY_ID")
        s3_secret_key = getenv("RUNPOD_S3_SECRET_KEY")
        volume_info = self._api.get_network_volume(self._network_volume_id)
        self._region = volume_info["dataCenterId"]
        s3_endpoint_url = f"https://s3api-{self._region.lower()}.runpod.io/"
        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=s3_access_key_id,
            aws_secret_access_key=s3_secret_key,
            endpoint_url=s3_endpoint_url,
            region_name=self._region,
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
            pod = self._api.get_pod(pod_id)
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
        return str(ip), int(port)

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

    def _get_gpu_id(self, gpu_type: str | int) -> Tuple[str, str]:
        query = str(gpu_type).strip().lower()
        if not query:
            raise ValueError("GPU type must not be empty")
        gpu_types = self._api.get_gpu_types()
        # Exact IDs/names take precedence over substring matches.
        matches = [gpu_id for gpu_id, name in gpu_types.items() if query in (gpu_id.lower(), name.lower())]
        if not matches:
            matches = [gpu_id for gpu_id, name in gpu_types.items() if query in gpu_id.lower() or query in name.lower()]
        if len(matches) == 1:
            return matches[0], gpu_types[matches[0]]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous GPU type: {gpu_type} matches {matches}. Use a full name or ID from rpc gpus.")
        raise ValueError(f"Unknown GPU type: {gpu_type}. Use rpc gpus to list GPU names and IDs.")

    def gpus(self) -> None:
        """List GPU names and IDs from RunPod's catalog."""
        for gpu_id, name in sorted(self._api.get_gpu_types().items()):
            print(f"{name}\t{gpu_id}")

    def list(self, verbose: bool = False) -> None:
        """List all pods in your RunPod account.

        Displays information about each pod including ID, name, GPU type, status, and connection details.
        """
        pods = self._api.get_pods()

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

    def teams(self) -> None:
        """List teams you belong to, showing team IDs for use with RUNPOD_TEAM_ID."""
        teams = self._api.get_teams()
        if not teams:
            logging.info("You are not a member of any teams.")
            return
        for team in teams:
            logging.info(f"Team: {team.get('name')}")
            logging.info(f"  ID: {team.get('id')}")
            logging.info("")

    def create(
        self,
        name: Optional[str] = None,
        runtime: Optional[int] = None,
        gpu_type: Optional[str] = None,
        cpus: Optional[int] = None,
        disk: Optional[int] = None,
        forward_agent: Optional[bool] = None,
        image_name: Optional[str] = None,
        memory: Optional[int] = None,
        num_gpus: Optional[int] = None,
        ssh_keys: Optional[str] = None,
        update_known_hosts: bool = True,
        update_ssh_config: bool = True,
        volume_mount_path: str = "/network",
        bashrc_line: Optional[str] = None,
    ) -> None:
        """Create a new RunPod instance with the specified parameters.

        Each default below can be overridden with an RPC_DEFAULT_* variable in your
        .env file (see .env.example); explicit CLI flags always take precedence.

        Args:
            runtime: Time in minutes for pod to run (default: 60, RPC_DEFAULT_RUNTIME)
            gpu_type: GPU type, or "CPU" for CPU-only pod (default: "RTX A4000", RPC_DEFAULT_GPU_TYPE)
            num_gpus: Number of GPUs (default: 1, RPC_DEFAULT_NUM_GPUS)
            name: Name for the pod (default: "$USER-$GPU_TYPE")
            disk: Container disk size in GB (default: 20, max 20 for CPU pods, RPC_DEFAULT_DISK)
            cpus: Minimum vCPU count (default: 2, RPC_DEFAULT_CPUS)
            memory: Minimum RAM in GB (default: 16, RPC_DEFAULT_MEMORY)
            ssh_keys: Path(s) to SSH public key file(s), space-separated, supports wildcards
                (default: use RunPod account keys, RPC_DEFAULT_SSH_PUBLIC_KEY_PATH)
            forward_agent: Whether to forward SSH agent (default: False, RPC_DEFAULT_FORWARD_AGENT)
            update_known_hosts: Whether to update known hosts (default: True)
            update_ssh_config: Whether to update SSH config (default: True)
            image_name: Docker image (default: "PyTorch 2.8.0 with CUDA 12.8.1", RPC_DEFAULT_IMAGE_NAME)
            bashrc_line: Line to append to the pod user's ~/.bashrc (RPC_DEFAULT_BASHRC_LINE),
                e.g. --bashrc_line='export PATH="$HOME/bin:$PATH"'

        Example:
            rpc create -r 60 -g "A100 SXM"
            rpc create --gpu_type="RTX A4000" --runtime=480
            rpc create --gpu_type=CPU
            rpc create --gpu_type=CPU --cpus=8 --memory=64
            rpc create --ssh_keys=~/.ssh/id_ed25519.pub
            rpc create --ssh_keys="~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub"
            rpc create --ssh_keys="~/.ssh/*.pub"
        """
        runtime = env_default(runtime, "RPC_DEFAULT_RUNTIME", 60)
        gpu_type = env_default(gpu_type, "RPC_DEFAULT_GPU_TYPE", "RTX A4000")
        cpus = env_default(cpus, "RPC_DEFAULT_CPUS", 2)
        disk = env_default(disk, "RPC_DEFAULT_DISK", 20)
        forward_agent = env_default(forward_agent, "RPC_DEFAULT_FORWARD_AGENT", False)
        image_name = env_default(image_name, "RPC_DEFAULT_IMAGE_NAME", DEFAULT_IMAGE_NAME)
        memory = env_default(memory, "RPC_DEFAULT_MEMORY", 16)
        num_gpus = env_default(num_gpus, "RPC_DEFAULT_NUM_GPUS", 1)
        ssh_keys = env_default(ssh_keys, "RPC_DEFAULT_SSH_PUBLIC_KEY_PATH", None)
        bashrc_line = env_default(bashrc_line, "RPC_DEFAULT_BASHRC_LINE", None)

        # Restart the SSH alias numbering when no pods exist
        if update_ssh_config:
            logging.info("Checking for existing pods...")
            start = time.monotonic()
            pods = self._api.get_pods()
            logging.info(f"...found {len(pods)} pod(s) in {time.monotonic() - start:.1f}s")
            if not pods:
                self.reset()

        # Handle CPU-only pods (convert to str in case Fire passes an int like 4090)
        if str(gpu_type).upper() == "CPU":
            gpu_type_id = None
            gpu_display_name = "CPU"
        else:
            gpu_type_id, gpu_display_name = self._get_gpu_id(str(gpu_type))

        name = name or f"{os.getenv('USER')}-{gpu_display_name}"
        runpodcli_dir = f".tmp_{name.replace(' ', '_')}"

        logging.info("Creating pod with:")
        logging.info(f"  Name: {name}")
        logging.info(f"  Image: {image_name}")
        logging.info(f"  Network volume ID: {self._network_volume_id}")
        logging.info(f"  Region: {self._region}")
        if gpu_type_id:
            logging.info(f"  GPU Type: {gpu_display_name}")
            logging.info(f"  GPU Count: {num_gpus}")
        else:
            logging.info(f"  CPU-only pod")
        logging.info(f"  Min vCPU: {cpus}")
        logging.info(f"  Min Memory: {memory} GB")
        logging.info(f"  Disk: {min(disk, 20) if not gpu_type_id else disk} GB")
        logging.info(f"  runpodcli directory: {runpodcli_dir}")
        logging.info(f"  Time limit: {runtime} minutes")

        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")
        remote_scripts_path = f"{volume_mount_path}/{runpodcli_dir}"
        scripts = [
            get_setup_root(remote_scripts_path, volume_mount_path),
            get_setup_user(remote_scripts_path, git_email, git_name, bashrc_line),
            get_start(remote_scripts_path),
            get_terminate(remote_scripts_path),
        ]
        for script_name, script_content in scripts:
            # s3_key is relative to /volume_mount_path, while remote_scripts_path is relative to /
            s3_key = f"{runpodcli_dir}/{script_name}"
            self._s3.put_object(Bucket=self._network_volume_id, Key=s3_key, Body=script_content.encode("utf-8"))

        docker_args = self._build_docker_args(volume_mount_path=volume_mount_path, runpodcli_dir=runpodcli_dir, runtime=runtime)

        # Set up environment variables - use provided key files or fetch from RunPod account
        if ssh_keys:
            # Read SSH keys from file(s) - supports wildcards and space-separated paths
            key_contents = []
            key_files = []
            for pattern in ssh_keys.split():
                pattern = os.path.expanduser(pattern.strip())
                paths = glob.glob(pattern)
                if not paths:
                    raise FileNotFoundError(f"No files matching: {pattern}")
                for path in sorted(paths):
                    with open(path) as f:
                        key_contents.append(f.read().strip())
                    key_files.append(path)
            logging.info(f"Using SSH keys from: {', '.join(key_files)}")
            public_keys = "\n".join(key_contents)
        else:
            logging.info("Using SSH keys from RunPod account")
            public_keys = self._api.get_pub_key()
        env = {"PUBLIC_KEY": public_keys} if public_keys else None

        pod = self._api.create_pod(
            name=name,
            image_name=image_name,
            gpu_type_id=gpu_type_id,
            gpu_count=num_gpus,
            container_disk_in_gb=disk,
            min_vcpu_count=cpus,
            min_memory_in_gb=memory,
            docker_args=docker_args,
            ports="8888/http,22/tcp",
            volume_mount_path=volume_mount_path,
            network_volume_id=self._network_volume_id,
            env=env,
        )

        pod_id: str = pod.get("id")  # type: ignore
        logging.info("Pod created. Provisioning...")
        pod = self._provision_and_wait(pod_id)
        logging.info("Pod provisioned.")

        ip, port = self._get_public_ip_and_port(pod)

        if update_ssh_config:
            self._write_ssh_config(ip, port, forward_agent)

        if update_known_hosts:
            if public_keys:
                self._update_known_hosts_file(ip, port, runpodcli_dir)
            else:
                # start_pod.sh only generates host keys when PUBLIC_KEY is set
                logging.info("No SSH public keys, skipping known_hosts update")

    def _generate_ssh_config(self, ip: str, port: int, forward_agent: bool = False, host_aliases: str = "runpod") -> str:
        return textwrap.dedent(f"""
            Host {host_aliases}
              HostName {ip}
              User user
              Port {port}
              UserKnownHostsFile ~/.ssh/known_hosts ~/.ssh/known_hosts.runpod_cli
              {"ForwardAgent yes" if forward_agent else ""}
        """).strip()

    def _write_ssh_config(self, ip: str, port: int, forward_agent: bool, config_path: str = "~/.ssh/config.runpod_cli") -> None:
        # Each pod gets its own numbered config file and `runpod` lives in its
        # own default file; the main file only accumulates Include lines, so
        # nothing is ever read back.
        path = os.path.expanduser(config_path)
        numbers = [int(suffix) for name in glob.glob(f"{path}.*") if (suffix := name.rsplit(".", 1)[1]).isdigit()]
        number = max(numbers, default=0) + 1
        with open(f"{path}.{number}", "w") as dest:
            dest.write(self._generate_ssh_config(ip=ip, port=port, forward_agent=forward_agent, host_aliases=f"runpod{number}"))
        with open(f"{path}.default", "w") as dest:  # `runpod` always points to the most recent pod
            dest.write(self._generate_ssh_config(ip=ip, port=port, forward_agent=forward_agent, host_aliases="runpod"))
        # "w" on the first pod also clears any entry written by older versions
        with open(path, "w" if number == 1 else "a") as dest:
            if number == 1:
                dest.write(f"Include {config_path}.default\n")
            dest.write(f"Include {config_path}.{number}\n")
        logging.info(f"SSH config at {config_path} updated")
        logging.info(f"Connect with: ssh runpod (or: ssh runpod{number})")

    def reset(self, config_path: str = "~/.ssh/config.runpod_cli") -> None:
        """Delete all SSH config files written by runpod_cli and restart the alias numbering."""
        for name in glob.glob(os.path.expanduser(config_path) + "*"):
            os.remove(name)
            logging.info(f"Removed {name}")

    def _wait_for_host_keys(self, runpodcli_dir: str, timeout: float = 120.0, poll_interval: float = 2.0) -> None:
        # start_pod.sh uploads the ed25519 host key last, so once it appears
        # in S3 all host keys are available.
        logging.info("Waiting for SSH host keys...")
        start = time.monotonic()
        while True:
            try:
                self._s3.head_object(Bucket=self._network_volume_id, Key=f"{runpodcli_dir}/ssh_ed25519_host_key")
                logging.info(f"...host keys available after {time.monotonic() - start:.1f}s")
                return
            except Exception:
                if time.monotonic() - start >= timeout:
                    logging.warning(f"No SSH host keys after {timeout:.0f}s; adding whichever keys exist")
                    return
                time.sleep(poll_interval)

    def _update_known_hosts_file(self, public_ip: str, port: int, runpodcli_dir: str) -> None:
        self._wait_for_host_keys(runpodcli_dir)
        host_keys: List[Tuple[str, str]] = []
        for file in ["ssh_ed25519_host_key", "ssh_ecdsa_host_key", "ssh_rsa_host_key", "ssh_dsa_host_key"]:
            try:
                obj = self._s3.get_object(Bucket=self._network_volume_id, Key=f"{runpodcli_dir}/{file}")
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

    def pubkey(self) -> None:
        """Fetch and display SSH public keys from RunPod account.

        Example:
            rpc pubkey
        """
        pub_key = self._api.get_pub_key()
        if pub_key:
            print(pub_key)
        else:
            logging.info("No public keys found in RunPod account")

    def terminate(self, *pod_ids: str) -> None:
        """Terminate one or more RunPod instances.

        Args:
            pod_ids: IDs of the pods to terminate (space-separated)

        Example:
            rpc terminate abc123
            rpc terminate abc123 def456 ghi789
        """
        if not pod_ids:
            logging.error("No pod IDs provided")
            return
        for pod_id in pod_ids:
            logging.info(f"Terminating pod {pod_id}")
            self._api.terminate_pod(pod_id)


def main():
    try:
        fire.Fire(RunPodManager)
    except RunPodAPIError as error:
        logging.error("%s", error)
        raise SystemExit(error.exit_code) from None


if __name__ == "__main__":
    main()
