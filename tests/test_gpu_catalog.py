from unittest.mock import Mock, patch

import pytest
import requests

from runpod_cli.api import RunPodAPIError, RunPodGraphQL, RUNPOD_GPU_CATALOG_URL
from runpod_cli.cli import RunPodManager


GPUS = {"NVIDIA GeForce RTX 4090": "RTX 4090", "NVIDIA RTX A4000": "RTX A4000"}


def test_v2_names_and_authentication_are_used():
    response = Mock(status_code=200)
    response.json.return_value = {"gpus": [{"id": key, "name": name} for key, name in GPUS.items()]}
    with patch("runpod_cli.api.requests.get", return_value=response) as get:
        assert RunPodGraphQL("test", team_id="team").get_gpu_types() == GPUS
    get.assert_called_once_with(RUNPOD_GPU_CATALOG_URL,
                                headers={"Authorization": "Bearer test", "Content-Type": "application/json", "x-team-id": "team"},
                                timeout=10)


@pytest.mark.parametrize("payload", [{"gpus": []}, {"gpus": [{"id": "a", "displayName": "old field"}]},
                                     {"gpus": [{"id": "", "name": "bad"}]}, None])
def test_malformed_catalog_is_reported(payload):
    response = Mock(status_code=200)
    response.json.return_value = payload
    with patch("runpod_cli.api.requests.get", return_value=response):
        with pytest.raises(RunPodAPIError, match="invalid GPU catalog"):
            RunPodGraphQL("test").get_gpu_types()


def test_catalog_timeout_and_authentication_errors_are_readable():
    with patch("runpod_cli.api.requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(RunPodAPIError, match="Could not fetch GPU catalog"):
            RunPodGraphQL("test").get_gpu_types()
    with patch("runpod_cli.api.requests.get", return_value=Mock(status_code=401)):
        with pytest.raises(RunPodAPIError, match="HTTP 401"):
            RunPodGraphQL("test").get_gpu_types()


def test_fetch_is_announced_in_the_log(caplog):
    response = Mock(status_code=200)
    response.json.return_value = {"gpus": [{"id": key, "name": name} for key, name in GPUS.items()]}
    with patch("runpod_cli.api.requests.get", return_value=response), caplog.at_level("INFO"):
        RunPodGraphQL("test").get_gpu_types()
    assert any(RUNPOD_GPU_CATALOG_URL in message for message in caplog.messages)
    assert any("fetched 2 GPU types in" in message for message in caplog.messages)


@pytest.mark.parametrize("selector", [4090, "4090", "rtx 4090", "NVIDIA GeForce RTX 4090"])
def test_gpu_selectors_preserve_exact_and_fuzzy_matching(selector):
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_types.return_value = GPUS
    assert manager._get_gpu_id(selector) == ("NVIDIA GeForce RTX 4090", "RTX 4090")


def test_ambiguous_selector_does_not_pick_a_gpu():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_types.return_value = GPUS
    with pytest.raises(ValueError, match="Ambiguous"):
        manager._get_gpu_id("RTX")


def test_unknown_gpu_has_a_clear_error():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_types.return_value = GPUS
    with pytest.raises(ValueError, match="Unknown GPU type"):
        manager._get_gpu_id("nonexistent")
