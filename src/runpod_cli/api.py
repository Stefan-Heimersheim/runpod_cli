"""RunPod API client: REST v2, with GraphQL only for account queries v2 does not cover."""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

RUNPOD_REST_URL = "https://api.runpod.io/v2"
RUNPOD_GPU_CATALOG_URL = f"{RUNPOD_REST_URL}/catalog/gpus"
# Account fields (pubKey, teams) have no REST v2 equivalent yet
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"  # rp-migrate: keep-v1

# Error messages that mean "retry later", not "bad request"
CAPACITY_MARKERS = (
    "no longer any instances available",
    "no instances available",
    "not enough capacity",
)


class RunPodAPIError(RuntimeError):
    """An expected API failure that can be shown directly to CLI users."""

    exit_code = 1


class RunPodCapacityError(RunPodAPIError):
    """No matching instances are currently available; callers may retry later."""

    exit_code = 75

# CPU instance types: (instance_id, vcpus, memory_gb)
# Flavors: m = 8GB/vCPU, g = 4GB/vCPU, c = 2GB/vCPU
CPU_INSTANCES: List[Tuple[str, int, int]] = [
    # c variants (2GB/vCPU)
    ("cpu3c-2-4", 2, 4),
    ("cpu3c-4-8", 4, 8),
    ("cpu3c-8-16", 8, 16),
    ("cpu3c-16-32", 16, 32),
    ("cpu3c-32-64", 32, 64),
    # g variants (4GB/vCPU)
    ("cpu3g-2-8", 2, 8),
    ("cpu3g-4-16", 4, 16),
    ("cpu3g-8-32", 8, 32),
    ("cpu3g-16-64", 16, 64),
    ("cpu3g-32-128", 32, 128),
    # m variants (8GB/vCPU)
    ("cpu3m-2-16", 2, 16),
    ("cpu3m-4-32", 4, 32),
    ("cpu3m-8-64", 8, 64),
    ("cpu3m-16-128", 16, 128),
    ("cpu3m-32-256", 32, 256),
]


def select_cpu_instance(min_vcpus: int, min_memory_gb: int) -> str:
    """Select the smallest CPU instance that meets the requirements."""
    candidates = [
        (instance_id, vcpus, mem)
        for instance_id, vcpus, mem in CPU_INSTANCES
        if vcpus >= min_vcpus and mem >= min_memory_gb
    ]
    if not candidates:
        raise ValueError(
            f"No CPU instance available with {min_vcpus} vCPUs and {min_memory_gb}GB RAM. "
            f"Max available: 32 vCPUs, 256GB RAM."
        )
    # Sort by memory (smallest that fits), then by vcpus
    candidates.sort(key=lambda x: (x[2], x[1]))
    return candidates[0][0]


class RunPodAPI:
    """RunPod REST v2 client (GraphQL only where v2 has no equivalent)."""

    def __init__(self, api_key: str, team_id: Optional[str] = None) -> None:
        self._api_key = api_key
        self._headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if team_id:
            self._headers["x-team-id"] = team_id

    def _rest(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        try:
            response = requests.request(method, f"{RUNPOD_REST_URL}{path}", headers=self._headers, json=payload, timeout=60)
        except requests.RequestException as error:
            raise RunPodAPIError(f"RunPod API request failed: {error}") from error
        if response.ok:
            return response.json() if response.content else None
        # REST v2 errors are {"title", "status", "detail", "errors": [...]}
        try:
            body = response.json()
            detail = body.get("detail") or response.text
            field_errors = "; ".join(str(e) for e in body.get("errors") or [])
            message = f"{body.get('title', 'RunPod API error')} ({response.status_code}): {detail}"
            if field_errors:
                message += f" [{field_errors}]"
        except ValueError:
            message = f"RunPod API error ({response.status_code}): {response.text}"
        if any(marker in message.lower() for marker in CAPACITY_MARKERS):
            raise RunPodCapacityError(message)
        raise RunPodAPIError(message)

    def _graphql(self, query: str) -> Dict:
        # rp-migrate: keep-v1 — used only for account fields with no REST v2 route
        response = requests.post(RUNPOD_GRAPHQL_URL, headers=self._headers, json={"query": query})
        if response.status_code != 200:
            raise RunPodAPIError(f"RunPod API error ({response.status_code}): {response.text}")
        result = response.json()
        if result.get("errors"):
            messages = [error.get("message", "Unknown API error") for error in result["errors"]]
            raise RunPodAPIError("; ".join(messages))
        return result.get("data", {})

    def get_gpu_catalog(self) -> List[Dict]:
        """Fetch the GPU catalog with live pod availability from REST v2.

        Each entry carries `availability` (HIGH/MEDIUM/LOW/NONE) and, where
        RunPod reports it, per-datacenter stock under `dataCenters`.
        """
        logging.info(f"Fetching GPU catalog: GET {RUNPOD_GPU_CATALOG_URL} ...")
        start = time.monotonic()
        try:
            # product is required whenever availability is requested
            response = requests.get(RUNPOD_GPU_CATALOG_URL, headers=self._headers,
                                    params={"include": "AVAILABILITY", "product": "POD"}, timeout=10)
        except requests.RequestException as error:
            raise RunPodAPIError(f"Could not fetch GPU catalog: {error}") from error
        if response.status_code != 200:
            raise RunPodAPIError(f"GPU catalog request failed (HTTP {response.status_code}). Check your RunPod API key and retry.")
        try:
            gpus = response.json()["gpus"]
            if not gpus or not all(
                isinstance(gpu.get("id"), str) and gpu["id"].strip() and isinstance(gpu.get("name"), str) and gpu["name"].strip()
                for gpu in gpus
            ):
                raise ValueError("Empty or invalid GPU catalog")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise RunPodAPIError("RunPod returned an invalid GPU catalog") from error
        logging.info(f"...fetched {len(gpus)} GPU types in {time.monotonic() - start:.1f}s")
        return gpus

    def get_gpu_types(self) -> Dict[str, str]:
        """Fetch current GPU IDs mapped to display names from the REST v2 catalog."""
        return {gpu["id"]: gpu["name"] for gpu in self.get_gpu_catalog()}

    def get_pods(self) -> List[Dict]:
        return self._rest("GET", "/pods")["pods"]  # rp-migrate: ignore — v2 path via _rest helper

    def get_pod(self, pod_id: str) -> Dict:
        return self._rest("GET", f"/pods/{pod_id}")

    def create_pod(
        self,
        name: str,
        image_name: str,
        gpu_type_id: Optional[str] = None,
        cloud_type: str = "SECURE",
        gpu_count: int = 1,
        container_disk_in_gb: int = 20,
        min_vcpu_count: int = 2,
        min_memory_in_gb: int = 16,
        docker_args: Optional[str] = None,
        ports: Optional[str] = None,
        volume_mount_path: Optional[str] = None,
        network_volume_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Create a GPU or CPU pod.

        If gpu_type_id is None, creates a CPU-only pod with automatically
        selected instance type based on min_vcpu_count and min_memory_in_gb.
        """
        if gpu_type_id is None:
            # CPU-only pod: v2 takes a flavor id plus a vCPU count; memory
            # follows from the flavor (c=2, g=4, m=8 GB per vCPU).
            instance_id = select_cpu_instance(min_vcpu_count, min_memory_in_gb)
            flavor, vcpus, _memory = next(entry for entry in CPU_INSTANCES if entry[0] == instance_id)
            payload: Dict[str, Any] = {
                "name": name,
                "image": image_name,
                "cpu": {"id": flavor.split("-")[0], "vcpuCount": vcpus},  # rp-migrate: ignore — vcpuCount is the v2 name
                "disk": min(container_disk_in_gb, 20),  # CPU pods max 20GB
            }
        else:
            # GPU pod: vCPU/memory minimums are per GPU in v2 (equivalent to
            # the old pod-wide minimums for single-GPU pods)
            payload = {
                "name": name,
                "image": image_name,
                "cloud": cloud_type,
                "gpu": {
                    "id": gpu_type_id,
                    "count": gpu_count,
                    "minVcpuCountPerGpu": min_vcpu_count,
                    "minRamPerGpu": min_memory_in_gb,
                },
                "disk": container_disk_in_gb,
            }

        # Shared optional fields
        if docker_args:
            payload["args"] = docker_args
        if ports:
            payload["ports"] = [port.strip() for port in ports.split(",")]
        if network_volume_id:
            if not volume_mount_path:
                raise ValueError("volume_mount_path is required with network_volume_id (v2 has no default mount path)")
            payload["mounts"] = {"network": [{"volumeId": network_volume_id, "path": volume_mount_path}]}
        if env:
            payload["env"] = env

        return self._rest("POST", "/pods", payload)  # rp-migrate: ignore — v2 path via _rest helper

    def terminate_pod(self, pod_id: str) -> None:
        self._rest("DELETE", f"/pods/{pod_id}")  # rp-migrate: ignore — v2 path via _rest helper

    def get_teams(self) -> List[Dict]:
        # teams are account data with no REST v2 route
        data = self._graphql("query { myself { teams { id name } } }")  # rp-migrate: keep-v1
        return data.get("myself", {}).get("teams", [])  # rp-migrate: keep-v1

    def get_network_volume(self, volume_id: str) -> Dict:
        volumes = self._rest("GET", "/network-volumes")["networkVolumes"]
        for vol in volumes:
            if vol.get("id") == volume_id:
                return vol
        raise ValueError(f"Network volume {volume_id} not found")

    def get_pub_key(self) -> Optional[str]:
        # account SSH keys have no REST v2 route
        data = self._graphql("query { myself { pubKey } }")  # rp-migrate: keep-v1
        return data.get("myself", {}).get("pubKey")  # rp-migrate: keep-v1
