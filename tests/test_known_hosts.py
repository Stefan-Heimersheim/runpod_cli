import base64
import json
from unittest.mock import Mock, patch

import pytest

from runpod_cli.api import RunPodAPI, RunPodAPIError
from runpod_cli.cli import RunPodManager


def public_key(algorithm='ssh-ed25519'):
    name = algorithm.encode()
    return base64.b64encode(len(name).to_bytes(4, 'big') + name + (32).to_bytes(4, 'big') + bytes(range(32))).decode()


def test_host_keys_from_logs_written_and_stream_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    key = public_key()
    closed = []
    def stream():
        try:
            yield 'ordinary log output'
            yield 'RUNPOD_CLI_HOST_KEY ssh-ed25519'  # too short
            yield 'RUNPOD_CLI_HOST_KEY sk-ssh-unknown ' + key  # not a known_hosts algorithm
            yield 'RUNPOD_CLI_HOST_KEY ssh-ed25519 ' + key + ' root@pod'
            yield 'RUNPOD_CLI_HOST_KEY ssh-ed25519 ' + key
            yield 'RUNPOD_CLI_HOST_KEYS_END'
            raise AssertionError('must stop at end marker')
        finally:
            closed.append(True)
    manager._api.get_pod_log_lines.return_value = stream()
    manager._update_known_hosts_file('pod', '192.0.2.1', 2201)
    assert (tmp_path / '.ssh/known_hosts.runpod_cli').read_text() == f'[192.0.2.1]:2201 ssh-ed25519 {key}\n'
    assert closed == [True]


@pytest.mark.parametrize('lines', [[], ['RUNPOD_CLI_HOST_KEY ssh-ed25519 ' + public_key()],
                                  ['RUNPOD_CLI_HOST_KEYS_END']])
def test_incomplete_or_empty_keys_leave_known_hosts_unchanged(tmp_path, monkeypatch, lines):
    monkeypatch.setenv('HOME', str(tmp_path))
    manager = RunPodManager.__new__(RunPodManager)
    manager._api = Mock()
    manager._api.get_pod_log_lines.return_value = (line for line in lines)
    manager._update_known_hosts_file('pod', '192.0.2.1', 2201)
    assert not (tmp_path / '.ssh/known_hosts.runpod_cli').exists()


def response(lines):
    result = Mock(ok=True)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.iter_lines.return_value = iter(lines)
    return result


def test_log_stream_parses_sse_and_reconnects():
    event = json.dumps({'source': 'container', 'line': 'hello'})
    first = response([': heartbeat', 'id: cursor-1', 'data: ' + event, ''])
    second = response(['data: invalid', '', 'data: {"source": "container", "line": "done"}', ''])
    with patch('runpod_cli.api.requests.get', side_effect=[first, second]) as get, patch('runpod_cli.api.time.sleep'):
        lines = RunPodAPI('token', team_id='team').get_pod_log_lines('pod')
        assert next(lines) == 'hello'
        assert next(lines) == 'done'
        lines.close()
    assert get.call_count == 2
    assert get.call_args.kwargs['headers']['Authorization'] == 'Bearer token'
    assert get.call_args.kwargs['headers']['x-team-id'] == 'team'
    assert get.call_args.kwargs['params'] == {'source': 'container', 'tail': 5000}
    first.__exit__.assert_called_once()
    second.__exit__.assert_called_once()


def test_log_api_errors_are_reported():
    result = response([])
    result.ok = False
    result.status_code = 403
    with patch('runpod_cli.api.requests.get', return_value=result), pytest.raises(RunPodAPIError, match='403'):
        list(RunPodAPI('token').get_pod_log_lines('pod'))


def test_log_stream_deadline_expires():
    with patch('runpod_cli.api.time.monotonic', side_effect=[0, 121]), patch('runpod_cli.api.requests.get') as get:
        assert list(RunPodAPI('token').get_pod_log_lines('pod')) == []
    get.assert_not_called()
