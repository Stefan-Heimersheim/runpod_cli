import logging
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from runpod_cli.api import RunPodAPI, RunPodAPIError, RunPodCapacityError, RunPodConfigError
from runpod_cli.cli import main


def rest_response(status_code, body):
    response = Mock(status_code=status_code, ok=status_code < 400, content=b"x")
    response.json.return_value = body
    return response


def test_capacity_error_has_readable_message():
    detail = "There are no longer any instances available with the requested specifications. Please refresh and try again."
    response = rest_response(500, {"title": "Internal Server Error", "status": 500, "detail": detail})
    with patch("runpod_cli.api.requests.request", return_value=response):
        with pytest.raises(RunPodCapacityError) as error:
            RunPodAPI("test").create_pod("test", "image", "gpu")
    assert detail in str(error.value)
    assert error.value.exit_code == 75


def test_validation_error_lists_field_errors():
    # rp-migrate: ignore start — a fake 422 body quoting a legacy field name, not a call site
    body = {"title": "Unprocessable Entity", "status": 422, "detail": "request body has an error",
            "errors": ["property 'imageName' is unsupported"]}
    with patch("runpod_cli.api.requests.request", return_value=rest_response(422, body)):
        with pytest.raises(RunPodAPIError, match="imageName.*unsupported") as error:
            # rp-migrate: ignore end
            RunPodAPI("test").create_pod("test", "image", "gpu")
    assert not isinstance(error.value, RunPodCapacityError)


def test_non_json_error_is_still_readable():
    response = Mock(status_code=502, ok=False, content=b"x", text="Bad Gateway")
    response.json.side_effect = ValueError("not json")
    with patch("runpod_cli.api.requests.request", return_value=response):
        with pytest.raises(RunPodAPIError, match="502.*Bad Gateway"):
            RunPodAPI("test").get_pods()


@pytest.mark.parametrize("detail, expected_code", [
    ("There are no longer any instances available with the requested specifications.", 75),
    ("Invalid API key", 1),
])
def test_process_exit_code_and_output(detail, expected_code):
    script = '''
import logging
from unittest.mock import Mock, patch
from runpod_cli.api import RunPodAPI
from runpod_cli.cli import main
logging.getLogger().handlers.clear()
logging.basicConfig(format="[%(levelname)s] %(message)s")
response = Mock(status_code=500, ok=False, content=b"x")
response.json.return_value = {"title": "Error", "status": 500, "detail": DETAIL}
def create(*args, **kwargs):
    RunPodAPI("test").create_pod("test", "image", "gpu")
with patch("runpod_cli.api.requests.request", return_value=response), patch("runpod_cli.cli.fire.Fire", side_effect=create):
    main()
'''.replace("DETAIL", repr(detail))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == expected_code
    assert detail in result.stderr
    assert result.stderr.startswith("[ERROR] ")
    assert "Traceback" not in result.stderr


def test_cli_reports_api_error_and_exits_unsuccessfully(caplog):
    with patch("runpod_cli.cli.fire.Fire", side_effect=RunPodAPIError("No instances available")):
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 1
    assert "No instances available" in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_cli_reports_config_errors_without_traceback(caplog):
    with patch("runpod_cli.cli.fire.Fire", side_effect=RunPodConfigError("Ambiguous GPU type: 4000")):
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 1
    assert "Ambiguous GPU type" in caplog.text and "Traceback" not in caplog.text


def test_unexpected_errors_are_not_hidden():
    with patch("runpod_cli.cli.fire.Fire", side_effect=TypeError("bug")):
        with pytest.raises(TypeError, match="bug"):
            main()


def test_plain_value_errors_are_not_hidden():
    with patch("runpod_cli.cli.fire.Fire", side_effect=ValueError("bug")):
        with pytest.raises(ValueError, match="bug"):
            main()


def test_get_pods_unwraps_v2_envelope():
    with patch("runpod_cli.api.requests.request", return_value=rest_response(200, {"pods": [{"id": "pod"}]})):  # rp-migrate: ignore — v2 envelope
        assert RunPodAPI("test").get_pods() == [{"id": "pod"}]  # rp-migrate: ignore — v2 envelope unwrap


def test_pub_key_still_uses_graphql():
    # rp-migrate: keep-v1 start — account SSH keys have no REST v2 route
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"myself": {"pubKey": "ssh-ed25519 AAAA"}}}
    with patch("runpod_cli.api.requests.post", return_value=response) as post:
        assert RunPodAPI("test").get_pub_key() == "ssh-ed25519 AAAA"
    assert post.call_args.args[0] == "https://api.runpod.io/graphql"
    # rp-migrate: keep-v1 end
