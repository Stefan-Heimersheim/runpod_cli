from unittest.mock import Mock

import pytest

from runpod_cli.cli import RunPodManager


@pytest.fixture
def manager():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pub_key.return_value = ""
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU"
    return manager


def test_skip_checks_uses_gpu_type_as_id_without_pod_or_catalog_lookups(manager):
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="NVIDIA RTX A4000", name="test", skip_checks=True)
    manager._api.get_pods.assert_not_called()
    manager._api.get_gpu_catalog.assert_not_called()
    assert manager._api.create_pod.call_args.kwargs["gpu_type_id"] == "NVIDIA RTX A4000"
