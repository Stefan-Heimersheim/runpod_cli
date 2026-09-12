import pytest

from runpod_cli.api import RunPodConfigError
from runpod_cli.cli import reject_unknown_flags


@pytest.mark.parametrize("argv", [
    ["create", "-g", "NVIDIA GeForce RTX 4090", "-num_g", "2"],  # created a 1-GPU pod, then errored
    ["create", "--num_gpu=2"],
    ["create", "--gpu", "x"],  # Fire has no prefix abbreviations
    ["create", "-n", "2"],  # n is ambiguous (name, num_gpus)
    ["list", "--verbos"],
])
def test_unknown_flags_are_rejected_before_running(argv):
    with pytest.raises(RunPodConfigError, match="Unknown flag"):
        reject_unknown_flags(argv)


@pytest.mark.parametrize("argv", [
    ["create", "-g", "RTX A4000", "--num_gpus=2", "-r", "60", "--noforward_agent", "--update_ssh_config=False"],
    ["--env=/tmp/x.env", "create", "--gpu_type", "CPU"],
    ["create", "--", "--help"],
    ["create", "-h"],
    ["list", "-v"],
    ["terminate", "abc123", "def456"],
    ["gpus"],
    [],
])
def test_known_flags_pass(argv):
    reject_unknown_flags(argv)
