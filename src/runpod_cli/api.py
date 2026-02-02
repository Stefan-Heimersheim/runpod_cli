"""GraphQL API client for RunPod."""

import json
from typing import Any, Dict, List, Optional

import requests

RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"


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
            raise RuntimeError(f"RunPod GraphQL error ({response.status_code}): {response.text}")
        result = response.json()
        if "errors" in result:
            raise RuntimeError(f"RunPod GraphQL error: {json.dumps(result['errors'])}")
        return result.get("data", {})

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
        container_disk_in_gb: int = 30,
        min_vcpu_count: int = 1,
        min_memory_in_gb: int = 1,
        docker_args: Optional[str] = None,
        ports: Optional[str] = None,
        volume_mount_path: Optional[str] = None,
        network_volume_id: Optional[str] = None,
        data_center_id: Optional[str] = None,
    ) -> Dict:
        query = """
        mutation PodFindAndDeployOnDemand($input: PodFindAndDeployOnDemandInput) {
          podFindAndDeployOnDemand(input: $input) {
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
                "cloudType": cloud_type,
                "gpuCount": gpu_count,
                "containerDiskInGb": container_disk_in_gb,
                "minVcpuCount": min_vcpu_count,
                "minMemoryInGb": min_memory_in_gb,
            }
        }
        if gpu_type_id:
            variables["input"]["gpuTypeId"] = gpu_type_id
        if data_center_id:
            variables["input"]["dataCenterId"] = data_center_id
        if docker_args:
            variables["input"]["dockerArgs"] = docker_args
        if ports:
            variables["input"]["ports"] = ports
        if volume_mount_path:
            variables["input"]["volumeMountPath"] = volume_mount_path
        if network_volume_id:
            variables["input"]["networkVolumeId"] = network_volume_id
        data = self._request(query, variables)
        return data.get("podFindAndDeployOnDemand", {})

    def create_cpu_pod(
        self,
        name: str,
        image_name: str,
        instance_id: str = "cpu3c-2-4",
        container_disk_in_gb: int = 30,
        docker_args: Optional[str] = None,
        ports: Optional[str] = None,
        volume_mount_path: Optional[str] = None,
        network_volume_id: Optional[str] = None,
        data_center_id: Optional[str] = None,
    ) -> Dict:
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
                "containerDiskInGb": container_disk_in_gb,
            }
        }
        if data_center_id:
            variables["input"]["dataCenterId"] = data_center_id
        if docker_args:
            variables["input"]["dockerArgs"] = docker_args
        if ports:
            variables["input"]["ports"] = ports
        if volume_mount_path:
            variables["input"]["volumeMountPath"] = volume_mount_path
        if network_volume_id:
            variables["input"]["networkVolumeId"] = network_volume_id
        data = self._request(query, variables)
        return data.get("deployCpuPod", {})

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
