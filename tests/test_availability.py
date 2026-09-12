from unittest.mock import Mock

import pytest

from runpod_cli.api import RunPodCapacityError
from runpod_cli.cli import RunPodManager


def make_manager(catalog):
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_catalog.return_value = catalog
    manager._api.get_pods.return_value = [{"id": "existing"}]
    manager._api.get_pub_key.return_value = ""
    manager._api.create_pod.side_effect = RuntimeError("stop before provisioning")
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    manager._region = "EU-RO-1"
    return manager


def gpu_entry(**extra):
    return {"id": "NVIDIA RTX A4000", "name": "RTX A4000", **extra}


def test_no_stock_in_volume_datacenter_exits_75_before_creating():
    manager = make_manager([gpu_entry(availability="LOW", dataCenters=[{"id": "EU-RO-1", "availability": "NONE"}])])
    with pytest.raises(RunPodCapacityError, match="no availability in EU-RO-1") as error:
        manager.create(gpu_type="A4000", name="test")
    assert error.value.exit_code == 75
    manager._api.create_pod.assert_not_called()
    manager._s3.put_object.assert_not_called()  # nothing uploaded either


def test_datacenter_stock_overrides_global_none():
    # Live catalogs report e.g. global NONE with LOW in one datacenter
    manager = make_manager([gpu_entry(availability="NONE", dataCenters=[{"id": "EU-RO-1", "availability": "LOW"}])])
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="A4000", name="test")
    manager._api.create_pod.assert_called_once()


def test_global_none_without_datacenter_data_blocks():
    manager = make_manager([gpu_entry(availability="NONE")])
    with pytest.raises(RunPodCapacityError, match="no availability overall"):
        manager.create(gpu_type="A4000", name="test")


def test_missing_availability_data_does_not_block():
    manager = make_manager([gpu_entry()])
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="A4000", name="test")


def test_check_can_be_disabled():
    manager = make_manager([gpu_entry(availability="NONE")])
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        manager.create(gpu_type="A4000", name="test", check_availability=False)


def test_gpus_lists_all_gpus_rentable_in_datacenter_last_and_cheapest_first(capsys):
    manager = make_manager([
        gpu_entry(availability="LOW", memory=16, price={"secure": 0.32, "community": 0.2},
                  dataCenters=[{"id": "EU-RO-1", "availability": "HIGH"}]),
        {"id": "NVIDIA L4", "name": "L4", "availability": "LOW", "memory": 24, "price": {"secure": 0.43},
         "dataCenters": [{"id": "EU-RO-1", "availability": "LOW"}]},
        {"id": "NVIDIA H100 80GB HBM3", "name": "H100 SXM", "availability": "MEDIUM", "memory": 80,
         "price": {"secure": 2.69}, "dataCenters": [{"id": "EU-RO-1", "availability": "NONE"}]},
        {"id": "NVIDIA B200", "name": "B200", "availability": "NONE", "memory": 180, "price": {"secure": 5.98}},
        {"id": "NVIDIA A40", "name": "A40", "availability": "LOW", "memory": 48, "price": {"secure": 0}},
        {"id": "unknown", "name": "unknown"},
    ])
    manager.gpus()
    # H100 (NONE) and B200/A40 (no datacenter data) come first, GPUs rentable in EU-RO-1 last,
    # each group cheapest first with unknown (zero) prices at the end; the "unknown" placeholder is dropped
    assert capsys.readouterr().out == (
        "| Name      | ID                    | VRAM   | $/h  | Overall | EU-RO-1 |\n"
        "| --------- | --------------------- | ------ | ---- | ------- | ------- |\n"
        "| H100 SXM  | NVIDIA H100 80GB HBM3 | 80 GB  | 2.69 | MEDIUM  | NONE    |\n"
        "| B200      | NVIDIA B200           | 180 GB | 5.98 | NONE    | -       |\n"
        "| A40       | NVIDIA A40            | 48 GB  | ?    | LOW     | -       |\n"
        "| RTX A4000 | NVIDIA RTX A4000      | 16 GB  | 0.32 | LOW     | HIGH    |\n"
        "| L4        | NVIDIA L4             | 24 GB  | 0.43 | LOW     | LOW     |\n"
    )
