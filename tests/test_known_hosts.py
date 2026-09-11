import io
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from runpod_cli.cli import RunPodManager


NOT_FOUND = ClientError({"Error": {"Code": "404"}}, "HeadObject")


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    manager = RunPodManager.__new__(RunPodManager)
    manager._s3 = Mock()
    manager._network_volume_id = "vol"
    return manager


def s3_object(text):
    return {"Body": io.BytesIO(text.encode())}


def test_waits_until_host_keys_appear(manager, monkeypatch, tmp_path):
    sleeps = []
    monkeypatch.setattr("runpod_cli.cli.time.sleep", sleeps.append)
    manager._s3.head_object.side_effect = [NOT_FOUND, NOT_FOUND, {}]
    manager._s3.get_object.side_effect = lambda Bucket, Key: (
        s3_object("ssh-ed25519 AAAAtestkey root@pod") if Key.endswith("ed25519_host_key") else (_ for _ in ()).throw(NOT_FOUND)
    )
    manager._update_known_hosts_file("192.0.2.1", 2201, ".tmp_test")
    assert sleeps == [2.0, 2.0]
    known_hosts = (tmp_path / ".ssh" / "known_hosts.runpod_cli").read_text()
    assert "[192.0.2.1]:2201 ssh-ed25519 AAAAtestkey" in known_hosts


def test_times_out_and_adds_available_keys(manager, monkeypatch, tmp_path):
    times = iter(range(0, 1000, 50))
    monkeypatch.setattr("runpod_cli.cli.time.monotonic", lambda: next(times))
    monkeypatch.setattr("runpod_cli.cli.time.sleep", lambda _: None)
    manager._s3.head_object.side_effect = NOT_FOUND
    manager._s3.get_object.side_effect = NOT_FOUND
    manager._update_known_hosts_file("192.0.2.1", 2201, ".tmp_test")
    assert not (tmp_path / ".ssh" / "known_hosts.runpod_cli").exists()
