"""Explicit, billable smoke test: create one cheap GPU pod, verify scripts, terminate it.

Run from the checkout: python scripts/test_live_transfer.py
Uses the usual rpc .env configuration and ~/.ssh/id_ed25519 (override with --ssh-key).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
from unittest.mock import patch

from runpod_cli.cli import RunPodManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env')
    parser.add_argument('--ssh-key', default='~/.ssh/id_ed25519')
    parser.add_argument('--max-hourly-price', type=float, default=0.30)
    parser.add_argument('--report', default='/tmp/runpod-transfer-report.json')
    args = parser.parse_args()
    key = Path(args.ssh_key).expanduser()
    if not key.is_file() or not Path(str(key) + '.pub').is_file():
        parser.error('SSH private key and matching .pub file must exist')
    manager = RunPodManager(env=args.env)
    # Prove transfer works without the obsolete credentials even if .env contains them.
    for name in ('RUNPOD_S3_ACCESS_KEY_ID', 'RUNPOD_S3_SECRET_KEY'):
        os.environ.pop(name, None)
    candidates = [gpu for gpu in manager._api.get_gpu_catalog()
                  if 0 < (gpu.get('price') or {}).get('secure', 0) <= args.max_hourly_price
                  and any(dc['id'] == manager._region and dc.get('availability') in ('LOW', 'MEDIUM', 'HIGH')
                          for dc in gpu.get('dataCenters', []))]
    if not candidates:
        raise RuntimeError('No GPU available in the volume region within the price limit')
    gpu = min(candidates, key=lambda gpu: gpu['price']['secure'])
    report = {'gpu': gpu['id'], 'region': manager._region, 'catalog_hourly_price': gpu['price']['secure']}
    print(json.dumps(report), flush=True)
    created = []
    expected = {}
    original_create = manager._api.create_pod
    original_build = manager._build_docker_args
    started = time.monotonic()

    def capture_create(**kwargs):
        pod = original_create(**kwargs)
        created.append(pod['id'])
        report['pod_id'] = pod['id']
        print('Created pod:', pod['id'], flush=True)
        return pod

    def capture_build(**kwargs):
        path = f"{kwargs['volume_mount_path']}/{kwargs['runpodcli_dir']}"
        report['scripts_directory'] = path
        for name, content in kwargs['scripts']:
            expected[f'{path}/{name}'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
        command = original_build(**kwargs)
        report['command_bytes'] = len(command.encode('utf-8'))
        return command

    try:
        with patch.object(manager._api, 'create_pod', capture_create), patch.object(manager, '_build_docker_args', capture_build):
            manager.create(name='rpc-base64-transfer-test', gpu_type=gpu['id'], runtime=10,
                           num_gpus=1, disk=20, cpus=2, memory=16, update_ssh_config=False,
                           ssh_keys=str(key) + '.pub', bashrc_line="export RPC_TRANSFER_TEST='quotes $HOME `literal` ☃'",
                           check_availability=True, skip_checks=False)
        pod = manager._api.get_pod(created[0])
        ip, port = manager._get_public_ip_and_port(pod)
        report['reported_cost'] = pod.get('cost')
        with tempfile.TemporaryDirectory(prefix='rpc-transfer-') as directory:
            ssh = ['ssh', '-F', '/dev/null', '-i', str(key), '-p', str(port),
                   '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                   '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={directory}/known_hosts',
                   f'root@{ip}']
            command = ('grep -q "Fast setup finished" ' + shlex.quote(report['scripts_directory'] + '/log.txt')
                       + ' && sha256sum ' + ' '.join(shlex.quote(path) for path in expected)
                       + ' && nvidia-smi --query-gpu=name --format=csv,noheader'
                       + ' && grep -F "RPC_TRANSFER_TEST=" /home/ubuntu/.bashrc')
            deadline = time.monotonic() + 300
            while True:
                result = subprocess.run(ssh + [command], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('SSH/setup timed out: ' + result.stderr[-1000:])
                print('Waiting for SSH and fast setup...', flush=True)
                time.sleep(5)
            actual = {}
            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1] in expected:
                    actual[parts[1]] = parts[0]
            if actual != expected:
                raise AssertionError('Remote hashes do not match local scripts')
            report['sha256'] = actual
            report['verified_files'] = len(actual)
            report['remote_output'] = result.stdout
            report['passed'] = True
            print(result.stdout, flush=True)
            # Only remove the unique directory created by this test.
            subprocess.run(ssh + ['rm -rf -- ' + shlex.quote(report['scripts_directory'])],
                           capture_output=True, timeout=30, check=True)
    finally:
        for pod_id in created:
            manager._api.terminate_pod(pod_id)
            report['terminated'] = True
            report['absent_from_pod_list'] = all(pod['id'] != pod_id for pod in manager._api.get_pods())
            print('Terminated test pod:', pod_id, flush=True)
        report['elapsed_seconds'] = round(time.monotonic() - started, 1)
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
        print('Report:', args.report, flush=True)


if __name__ == '__main__':
    main()
