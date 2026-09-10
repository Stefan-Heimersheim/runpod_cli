"""GraphQL API client for RunPod."""

from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from .catalog import get_gpu_types, valid_gpu_types
except ImportError:
    from catalog import get_gpu_types, valid_gpu_types  # type: ignore

RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"
RUNPOD_GPU_CATALOG_URL = "https://api.runpod.io/v2/catalog/gpus"


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


class RunPodGraphQL:
    """GraphQL API client for RunPod."""

    def __init__(self, api_key: str, team_id: Optional[str] = None) -> None:
        self._api_key = api_key
        self._headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if team_id:
            self._headers["x-team-id"] = team_id

    def _request(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict:
        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        response = requests.post(RUNPOD_GRAPHQL_URL, headers=self._headers, json=payload)
        if response.status_code != 200:
            raise RunPodAPIError(f"RunPod API error ({response.status_code}): {response.text}")
        result = response.json()
        if result.get("errors"):
            messages = [error.get("message", "Unknown API error") for error in result["errors"]]
            capacity_message = "there are no longer any instances available with the requested specifications"
            if all(message.lower().startswith(capacity_message) for message in messages):
                raise RunPodCapacityError("; ".join(messages))
            raise RunPodAPIError("; ".join(messages))
        return result.get("data", {})

    def get_gpu_types(self, refresh: bool = False) -> Dict[str, str]:
        """Return current GPU IDs mapped to display names, with a local disk cache."""
        return get_gpu_types(self._fetch_gpu_types, refresh=refresh)

    def _fetch_gpu_types(self) -> Dict[str, str]:
        try:
            response = requests.get(RUNPOD_GPU_CATALOG_URL, headers=self._headers, timeout=10)
        except requests.RequestException as error:
            raise RunPodAPIError(f"Could not fetch GPU catalog: {error}") from error
        if response.status_code != 200:
            raise RunPodAPIError(f"GPU catalog request failed (HTTP {response.status_code}). Check your RunPod API key and retry.")
        try:
            rows = response.json()["gpus"]
            gpu_types = {gpu["id"]: gpu["name"] for gpu in rows}
            if not valid_gpu_types(gpu_types):
                raise ValueError("Empty or invalid GPU catalog")
        except (ValueError, KeyError, TypeError) as error:
            raise RunPodAPIError("RunPod returned an invalid GPU catalog") from error
        return gpu_types

    def get_pods(self) -> List[Dict]:
        query = """
        query Pods {
          myself {
            pods {
              id
              name
              dockerArgs
              lastStatusChange
              runtime {
                uptimeInSeconds
                ports {
                  ip
                  isIpPublic
                  privatePort
                  publicPort
                  type
                }
              }
              gpuCount
              memoryInGb
              vcpuCount
              containerDiskInGb
              volumeMountPath
              costPerHr
              machine {
                gpuDisplayName
              }
            }
          }
        }
        """
        data = self._request(query)
        return data.get("myself", {}).get("pods", [])

    def get_pod(self, pod_id: str) -> Dict:
        query = """
        query Pod($input: PodFilter) {
          pod(input: $input) {
            id
            name
            dockerArgs
            lastStatusChange
            runtime {
              uptimeInSeconds
              ports {
                ip
                isIpPublic
                privatePort
                publicPort
                type
              }
            }
            gpuCount
            memoryInGb
            vcpuCount
            containerDiskInGb
            volumeMountPath
            costPerHr
            machine {
              gpuDisplayName
            }
          }
        }
        """
        data = self._request(query, {"input": {"podId": pod_id}})
        return data.get("pod", {})

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
            # CPU-only pod
            instance_id = select_cpu_instance(min_vcpu_count, min_memory_in_gb)
            query = """
            mutation DeployCpuPod($input: deployCpuPodInput!) {
              deployCpuPod(input: $input) {
                id
                imageName
                machineId
              }
            }
            """
            variables: Dict[str, Any] = {
                "input": {
                    "name": name,
                    "imageName": image_name,
                    "instanceId": instance_id,
                    "containerDiskInGb": min(container_disk_in_gb, 20),  # CPU pods max 20GB
                }
            }
            result_key = "deployCpuPod"
        else:
            # GPU pod
            query = """
            mutation PodFindAndDeployOnDemand($input: PodFindAndDeployOnDemandInput) {
              podFindAndDeployOnDemand(input: $input) {
                id
                imageName
                machineId
              }
            }
            """
            variables = {
                "input": {
                    "name": name,
                    "imageName": image_name,
                    "gpuTypeId": gpu_type_id,
                    "cloudType": cloud_type,
                    "gpuCount": gpu_count,
                    "containerDiskInGb": container_disk_in_gb,
                    "minVcpuCount": min_vcpu_count,
                    "minMemoryInGb": min_memory_in_gb,
                }
            }
            result_key = "podFindAndDeployOnDemand"

        # Shared optional fields
        if docker_args:
            variables["input"]["dockerArgs"] = docker_args
        if ports:
            variables["input"]["ports"] = ports
        if volume_mount_path:
            variables["input"]["volumeMountPath"] = volume_mount_path
        if network_volume_id:
            variables["input"]["networkVolumeId"] = network_volume_id
        if env:
            variables["input"]["env"] = [{"key": k, "value": v} for k, v in env.items()]

        data = self._request(query, variables)
        return data.get(result_key, {})

    def terminate_pod(self, pod_id: str) -> None:
        query = """
        mutation PodTerminate($input: PodTerminateInput!) {
          podTerminate(input: $input)
        }
        """
        self._request(query, {"input": {"podId": pod_id}})

    def get_teams(self) -> List[Dict]:
        query = """
        query {
          myself {
            teams {
              id
              name
            }
          }
        }
        """
        data = self._request(query)
        return data.get("myself", {}).get("teams", [])

    def get_network_volume(self, volume_id: str) -> Dict:
        query = """
        query {
          myself {
            networkVolumes {
              id
              name
              dataCenterId
            }
          }
        }
        """
        data = self._request(query)
        volumes = data.get("myself", {}).get("networkVolumes", [])
        for vol in volumes:
            if vol.get("id") == volume_id:
                return vol
        raise ValueError(f"Network volume {volume_id} not found")

    def get_pub_key(self) -> Optional[str]:
        query = """
        query {
          myself {
            pubKey
          }
        }
        """
        data = self._request(query)
        return data.get("myself", {}).get("pubKey")
