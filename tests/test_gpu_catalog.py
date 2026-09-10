import json
import threading
import time
from unittest.mock import Mock, patch

import pytest
import requests

from runpod_cli.api import RunPodAPIError, RunPodGraphQL, RUNPOD_GPU_CATALOG_URL
from runpod_cli.catalog import CACHE_TTL, get_gpu_types
from runpod_cli.cli import RunPodManager


GPUS = {"NVIDIA GeForce RTX 4090": "RTX 4090", "NVIDIA RTX A4000": "RTX A4000"}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


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


def test_warm_cache_performs_no_network_call_and_refresh_bypasses_it(tmp_path):
    fetch = Mock(return_value=GPUS)
    assert get_gpu_types(fetch) == GPUS
    assert get_gpu_types(fetch) == GPUS
    assert fetch.call_count == 1
    updated = dict(GPUS, new="New GPU")
    fetch.return_value = updated
    assert get_gpu_types(fetch, refresh=True) == updated
    assert fetch.call_count == 2
    cached = json.loads((tmp_path / "runpod_cli" / "gpu-types.json").read_text())
    assert cached["gpus"] == updated


def test_stale_cache_returns_before_background_fetch_completes(tmp_path):
    get_gpu_types(lambda: GPUS)
    path = tmp_path / "runpod_cli" / "gpu-types.json"
    path.write_text(json.dumps({"fetched_at": time.time() - CACHE_TTL - 1, "gpus": GPUS}))
    started, release = threading.Event(), threading.Event()
    workers = []
    real_thread = threading.Thread

    def track_thread(**kwargs):
        worker = real_thread(**kwargs)
        workers.append(worker)
        return worker

    def fetch():
        started.set()
        assert release.wait(5)
        return {"new": "New GPU"}

    try:
        with patch("runpod_cli.catalog.threading.Thread", side_effect=track_thread):
            assert get_gpu_types(fetch) == GPUS
        assert started.wait(1)
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
    assert json.loads(path.read_text())["gpus"] == {"new": "New GPU"}


def test_corrupt_cache_is_replaced_and_unwritable_cache_is_optional(tmp_path):
    directory = tmp_path / "runpod_cli"
    directory.mkdir()
    (directory / "gpu-types.json").write_text("not json")
    assert get_gpu_types(lambda: GPUS) == GPUS
    with patch("runpod_cli.catalog.tempfile.NamedTemporaryFile", side_effect=PermissionError):
        assert get_gpu_types(lambda: GPUS, refresh=True) == GPUS


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


def test_unknown_gpu_refreshes_catalog_once():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_types.side_effect = [GPUS, {"new": "New GPU"}]
    assert manager._get_gpu_id("new") == ("new", "New GPU")
    assert [call.kwargs for call in manager._api.get_gpu_types.call_args_list] == [{"refresh": False}, {"refresh": True}]


def test_still_unknown_gpu_has_a_clear_error():
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_gpu_types.return_value = GPUS
    with pytest.raises(ValueError, match="Unknown GPU type"):
        manager._get_gpu_id("nonexistent")
    assert manager._api.get_gpu_types.call_count == 2
