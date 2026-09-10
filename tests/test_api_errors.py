import logging
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from runpod_cli.api import RunPodAPIError, RunPodCapacityError, RunPodGraphQL
from runpod_cli.cli import main


def test_capacity_error_has_readable_message():
    message = "There are no longer any instances available with the requested specifications. Please refresh and try again."
    response = Mock(status_code=200)
    response.json.return_value = {"errors": [{"message": message}]}
    with patch("runpod_cli.api.requests.post", return_value=response):
        with pytest.raises(RunPodCapacityError) as error:
            RunPodGraphQL("test").create_pod("test", "image", "gpu")
    assert str(error.value) == message
    assert error.value.exit_code == 75


@pytest.mark.parametrize("messages, expected_code", [
    (["There are no longer any instances available with the requested specifications. Please refresh and try again."], 75),
    (["Invalid API key"], 1),
    (["There are no longer any instances available with the requested specifications.", "Invalid input"], 1),
])
def test_process_exit_code_and_output(messages, expected_code):
    script = '''
import logging
from unittest.mock import Mock, patch
from runpod_cli.api import RunPodGraphQL
from runpod_cli.cli import main
logging.getLogger().handlers.clear()
logging.basicConfig(format="[%(levelname)s] %(message)s")
response = Mock(status_code=200)
response.json.return_value = {"errors": [{"message": message} for message in MESSAGES]}
def create(*args, **kwargs):
    RunPodGraphQL("test").create_pod("test", "image", "gpu")
with patch("runpod_cli.api.requests.post", return_value=response), patch("runpod_cli.cli.fire.Fire", side_effect=create):
    main()
'''.replace("MESSAGES", repr(messages))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == expected_code
    assert result.stderr.strip() == "[ERROR] " + "; ".join(messages)
    assert "Traceback" not in result.stderr


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
