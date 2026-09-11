import glob
import logging
import os
import re
import textwrap
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import boto3
import fire
from dotenv import load_dotenv

try:
    from .api import RunPodAPIError, RunPodCapacityError, RunPodAPI
    from .utils import (
        DEFAULT_IMAGE_NAME,
        get_install,
        get_setup_root,
        get_setup_user,
        get_start,
        get_terminate,
    )
except ImportError:
    from api import RunPodAPIError, RunPodCapacityError, RunPodAPI  # type: ignore
    from utils import (  # type: ignore
        DEFAULT_IMAGE_NAME,
        get_install,
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

        self._api = RunPodAPI(getenv("RUNPOD_API_KEY"), team_id=os.getenv("RUNPOD_TEAM_ID"))
        self._network_volume_id: str = getenv("RUNPOD_NETWORK_VOLUME_ID")
        s3_access_key_id = getenv("RUNPOD_S3_ACCESS_KEY_ID")
        s3_secret_key = getenv("RUNPOD_S3_SECRET_KEY")
        volume_info = self._api.get_network_volume(self._network_volume_id)
        self._region = volume_info["dataCenter"]
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
            # v2 exposes lifecycle states, so a doomed pod fails in seconds
            # instead of timing out after n_attempts
            if pod.get("status") in ("ERROR", "TERMINATED"):
                raise RunPodAPIError(f"Pod {pod_id} entered status {pod['status']} during provisioning")
            pod_runtime = pod.get("runtime")
            if pod_runtime is None or not pod_runtime.get("ports"):
                time.sleep(5)
            else:
                return pod
        raise RuntimeError("Pod provisioning failed")

    def _get_public_ip_and_port(self, pod: Dict) -> Tuple[str, int]:
        # v2 runtime ports are {private, public, type, ip}; the pod's sshd
        # listens on 22/tcp, so that mapping is the one to put in SSH config
        publics = [p for p in pod["runtime"]["ports"] if p.get("ip") and p.get("public")]
        candidates = [p for p in publics if p.get("type") == "tcp" and p.get("private") == 22] or publics
        if len(candidates) != 1:
            raise ValueError(f"Expected 1 public IP, got {candidates}")
        return str(candidates[0]["ip"]), int(candidates[0]["public"])

    def _parse_time_remaining(self, pod: Dict) -> str:
        _sleep_re = re.compile(r"\bsleep\s+(\d+)\b")
        start_dt = None
        sleep_secs = None
        started_at = pod.get("startedAt")
        if isinstance(started_at, str) and started_at:
            try:
                start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
        args = pod.get("args", "")
        if isinstance(args, str):
            match = _sleep_re.search(args)
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

    def _get_gpu_id(self, gpu_type: str | int) -> Tuple[str, str, Dict]:
        query = str(gpu_type).strip().lower()
        if not query:
            raise ValueError("GPU type must not be empty")
        catalog = {gpu["id"]: gpu for gpu in self._api.get_gpu_catalog()}
        gpu_types = {gpu_id: gpu["name"] for gpu_id, gpu in catalog.items()}
        # Exact IDs/names take precedence over substring matches.
        matches = [gpu_id for gpu_id, name in gpu_types.items() if query in (gpu_id.lower(), name.lower())]
        if not matches:
            matches = [gpu_id for gpu_id, name in gpu_types.items() if query in gpu_id.lower() or query in name.lower()]
        if len(matches) == 1:
            return matches[0], gpu_types[matches[0]], catalog[matches[0]]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous GPU type: {gpu_type} matches {matches}. Use a full name or ID from rpc gpus.")
        raise ValueError(f"Unknown GPU type: {gpu_type}. Use rpc gpus to list GPU names and IDs.")

    def _region_availability(self, gpu_entry: Dict) -> Tuple[Optional[str], str]:
        """Best available stock signal: the volume's datacenter if reported, else overall."""
        for dc in gpu_entry.get("dataCenters") or []:
            if dc.get("id") == self._region:
                return dc.get("availability"), f"in {self._region}"
        return gpu_entry.get("availability"), "overall"

    def _check_gpu_availability(self, gpu_entry: Dict, gpu_display_name: str) -> None:
        availability, scope = self._region_availability(gpu_entry)
        if availability == "NONE":
            raise RunPodCapacityError(
                f"{gpu_display_name} has no availability {scope} right now (catalog preflight; nothing was created). "
                "Retry later, choose another GPU (rpc gpus --availability), or pass --check_availability=False to try anyway."
            )
        if availability:
            logging.info(f"  Availability {scope}: {availability}")

    def gpus(self, availability: bool = False) -> None:
        """List GPU names and IDs from RunPod's catalog.

        Args:
            availability: Also show live pod stock (HIGH/MEDIUM/LOW/NONE), overall and
                for your network volume's datacenter; GPUs without stock data for that
                datacenter are not rentable there and are hidden.
        """
        catalog = sorted(self._api.get_gpu_catalog(), key=lambda gpu: gpu["id"])
        if not availability:
            for gpu in catalog:
                print(f"{gpu['name']}\t{gpu['id']}")
            return
        rows = []
        for gpu in catalog:
            datacenters = {dc.get("id"): dc.get("availability") for dc in gpu.get("dataCenters") or []}
            region_availability = datacenters.get(self._region)
            if not region_availability:
                continue
            rows.append((gpu["name"], gpu["id"], gpu.get("availability") or "?", f"{self._region}:{region_availability}"))
        widths = [max(len(row[column]) for row in rows) for column in range(4)] if rows else []
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())

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
                hardware = pod.get("gpu") or pod.get("cpu") or {}
                if pod.get("gpu"):
                    logging.info(f"  GPUs: {hardware.get('count')} x {hardware.get('id')}")
                logging.info(f"  vcpuCount: {hardware.get('vcpuCount')}")  # rp-migrate: ignore — vcpuCount is also the v2 field name
                logging.info(f"  memory: {hardware.get('memory')} GB")
                network_mounts = (pod.get("mounts") or {}).get("network") or []
                if network_mounts:
                    logging.info(f"  mountPath: {network_mounts[0].get('path')}")
                for key in ["disk", "cost", "status"]:
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
        check_availability: Optional[bool] = None,
        skip_checks: Optional[bool] = None,
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
            image_name: Docker image (default: "PyTorch 2.8.0 with CUDA 12.8.1 on Ubuntu 24.04", RPC_DEFAULT_IMAGE_NAME)
            bashrc_line: Line to append to the pod user's ~/.bashrc (RPC_DEFAULT_BASHRC_LINE),
                e.g. --bashrc_line='export PATH="$HOME/bin:$PATH"'
            check_availability: Check catalog stock before creating a GPU pod and exit 75
                without creating anything when it is NONE (default: True, RPC_DEFAULT_CHECK_AVAILABILITY)
            skip_checks: Skip the existing-pod check and the GPU catalog lookup (including the
                availability preflight); gpu_type must then be an exact catalog ID such as
                "NVIDIA RTX A4000" (default: False, RPC_DEFAULT_SKIP_CHECKS)

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
        check_availability = env_default(check_availability, "RPC_DEFAULT_CHECK_AVAILABILITY", True)
        skip_checks = env_default(skip_checks, "RPC_DEFAULT_SKIP_CHECKS", False)

        # Restart the SSH alias numbering when no pods exist
        if update_ssh_config and not skip_checks:
            logging.info("Checking for existing pods...")
            start = time.monotonic()
            pods = self._api.get_pods()
            logging.info(f"...found {len(pods)} pod(s) in {time.monotonic() - start:.1f}s")
            if not pods:
                self.reset()
                self._cleanup_scripts_dirs()

        # Handle CPU-only pods (convert to str in case Fire passes an int like 4090)
        if str(gpu_type).upper() == "CPU":
            gpu_type_id = None
            gpu_display_name = "CPU"
        elif skip_checks:
            gpu_type_id = gpu_display_name = str(gpu_type)  # taken as the exact catalog ID
        else:
            gpu_type_id, gpu_display_name, gpu_entry = self._get_gpu_id(str(gpu_type))
            if check_availability:
                self._check_gpu_availability(gpu_entry, gpu_display_name)

        name = name or f"{os.getenv('USER')}-{gpu_display_name}"
        # Unique per pod, so same-named pods never share logs or host keys
        runpodcli_dir = f".tmp_{name.replace(' ', '_')}_{uuid.uuid4().hex[:8]}"

        git_email = os.getenv("GIT_EMAIL", "")
        git_name = os.getenv("GIT_NAME", "")
        # Sanitized local username, used to keep per-user state apart on team-shared volumes
        local_user = re.sub(r"[^A-Za-z0-9._-]", "_", os.getenv("USER") or "user")
        remote_scripts_path = f"{volume_mount_path}/{runpodcli_dir}"
        scripts = [
            get_setup_root(remote_scripts_path, volume_mount_path),
            get_setup_user(remote_scripts_path, git_email, git_name, bashrc_line, local_user),
            get_install(remote_scripts_path),
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
            public_keys = self._api.get_pub_key()
        env = {"PUBLIC_KEY": public_keys} if public_keys else None

        logging.info(f"Creating pod {name} ({runtime} minutes)...")
        start = time.monotonic()
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
        pod = self._provision_and_wait(pod_id)
        logging.info(f"...created and provisioned in {time.monotonic() - start:.1f}s")

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

    def _cleanup_scripts_dirs(self) -> None:
        """Delete leftover .tmp_* script directories from the network volume.

        Called when no pods exist, so nothing can still be using them. Stale
        directories otherwise accumulate old logs and, worse, old SSH host
        keys that the known_hosts polling could pick up for a new pod.
        """
        keys = []
        token = None
        while True:
            kwargs = {"Bucket": self._network_volume_id, "Prefix": ".tmp_"}
            if token:
                kwargs["ContinuationToken"] = token
            response = self._s3.list_objects_v2(**kwargs)
            keys += [{"Key": obj["Key"]} for obj in response.get("Contents", [])]
            token = response.get("NextContinuationToken")
            if not token:
                break
        for start in range(0, len(keys), 1000):
            self._s3.delete_objects(Bucket=self._network_volume_id, Delete={"Objects": keys[start:start + 1000]})
        if keys:
            logging.info(f"Removed {len(keys)} leftover script file(s) from the network volume")

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
