import logging
from unittest.mock import Mock, patch

import pytest

from runpod_cli.api import RunPodAPIError, RunPodGraphQL
from runpod_cli.cli import main


def test_capacity_error_has_readable_message():
    message = "There are no longer any instances available with the requested specifications. Please refresh and try again."
    response = Mock(status_code=200)
    response.json.return_value = {"errors": [{"message": message}]}
    with patch("runpod_cli.api.requests.post", return_value=response):
        with pytest.raises(RunPodAPIError) as error:
            RunPodGraphQL("test").create_pod("test", "image", "gpu")
    assert str(error.value) == message


def test_cli_reports_api_error_and_exits_unsuccessfully(caplog):
    with patch("runpod_cli.cli.fire.Fire", side_effect=RunPodAPIError("No instances available")):
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 1
    assert "No instances available" in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_unexpected_errors_are_not_hidden():
    with patch("runpod_cli.cli.fire.Fire", side_effect=TypeError("bug")):
        with pytest.raises(TypeError, match="bug"):
            main()


def test_empty_errors_do_not_mask_success():
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"myself": {"pods": [{"id": "pod"}]}}, "errors": []}
    with patch("runpod_cli.api.requests.post", return_value=response):
        assert RunPodGraphQL("test").get_pods() == [{"id": "pod"}]
