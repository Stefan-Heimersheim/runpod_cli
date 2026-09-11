from unittest.mock import Mock, patch

import pytest

from runpod_cli.api import RunPodAPI


def created(body):
    response = Mock(status_code=201, ok=True, content=b"x")
    response.json.return_value = body
    return response


def test_gpu_pod_payload_uses_v2_shapes():
    with patch("runpod_cli.api.requests.request", return_value=created({"id": "p1"})) as request:
        RunPodAPI("test").create_pod(
            name="n", image_name="img", gpu_type_id="NVIDIA RTX A4000", gpu_count=2,
            container_disk_in_gb=30, min_vcpu_count=4, min_memory_in_gb=32,
            docker_args="/bin/bash -c 'sleep 60'", ports="8888/http,22/tcp",
            volume_mount_path="/network", network_volume_id="vol1", env={"PUBLIC_KEY": "k"},
        )
    assert request.call_args.args == ("POST", "https://api.runpod.io/v2/pods")
    payload = request.call_args.kwargs["json"]
    assert payload["image"] == "img"
    assert payload["cloud"] == "SECURE"
    assert payload["gpu"] == {"id": "NVIDIA RTX A4000", "count": 2, "minVcpuCountPerGpu": 4, "minRamPerGpu": 32}
    assert payload["disk"] == 30
    assert payload["args"] == "/bin/bash -c 'sleep 60'"
    assert payload["ports"] == ["8888/http", "22/tcp"]  # array, not a comma string
    assert payload["mounts"] == {"network": [{"volumeId": "vol1", "path": "/network"}]}
    assert payload["env"] == {"PUBLIC_KEY": "k"}  # map, not [{key, value}]
    # rp-migrate: ignore start — asserting the legacy names are ABSENT from the payload
    for legacy in ["imageName", "gpuTypeId", "cloudType", "containerDiskInGb", "minVcpuCount",
                   "minMemoryInGb", "dockerArgs", "volumeMountPath", "networkVolumeId"]:
        assert legacy not in payload
    # rp-migrate: ignore end


def test_cpu_pod_payload_uses_flavor_and_vcpu_count():
    with patch("runpod_cli.api.requests.request", return_value=created({"id": "p1"})) as request:
        RunPodAPI("test").create_pod(name="n", image_name="img", gpu_type_id=None,
                                     container_disk_in_gb=40, min_vcpu_count=4, min_memory_in_gb=16)
    payload = request.call_args.kwargs["json"]
    assert payload["cpu"] == {"id": "cpu3g", "vcpuCount": 4}  # cpu3g-4-16 (4GB/vCPU)  # rp-migrate: ignore
    assert payload["disk"] == 20  # CPU pods max 20GB
    assert "gpu" not in payload and "cloud" not in payload


def test_network_volume_requires_mount_path():
    with pytest.raises(ValueError, match="volume_mount_path"):
        RunPodAPI("test").create_pod(name="n", image_name="img", gpu_type_id="g",
                                     network_volume_id="vol1", volume_mount_path=None)
