# Live pod smoke test recipe

Distilled from a throwaway script used for the
[S3-free startup validation](2026-09-15-s3-free-startup-validation.md) on 2026-09-15. The
script itself was deleted; this is what it knew. Live tests cost money and need permission from
the maintainer before creating a pod.

## Picking a pod

- Filter the GPU catalog to GPUs with secure-cloud price at or below about $0.30/hour and
  availability LOW, MEDIUM or HIGH in the network volume's region, then take the cheapest.
  Small RTX Ada / RTX PRO cards usually qualify; the default RTX A4000 is often not stocked.
- Create with `runtime=10` minutes, 1 GPU, 20 GB disk, 2 CPUs, 16 GB RAM, `update_ssh_config=False`,
  and an explicit `ssh_keys=<path>.pub`, because the devbox key is not in the RunPod account.
  A recognisable pod name (e.g. `rpc-<feature>-test`) makes stray pods easy to spot.
- Pass a `bashrc_line` containing quotes, `$HOME`, backticks and a Unicode character to check
  that shell escaping survives the transfer.

## Verifying the transfer

- Wrap `RunPodManager._build_docker_args` with `unittest.mock.patch.object` to capture the
  scripts and their SHA-256 hashes before they are sent, plus the startup command byte size.
  Wrap `_api.create_pod` the same way to record the pod ID for cleanup.
- SSH as root with `-F /dev/null`, `IdentitiesOnly=yes`, `BatchMode=yes` and a temporary
  `UserKnownHostsFile`, so the test never touches the user's SSH config or known_hosts.
- Poll every 5 seconds for up to 5 minutes until one command succeeds:
  `grep -q "Fast setup finished" <scripts_dir>/log.txt && sha256sum <scripts...> && nvidia-smi
  --query-gpu=name --format=csv,noheader && grep -F <marker> /home/ubuntu/.bashrc`.
  Compare remote hashes to the captured ones.
- The full run, including creation and cleanup, took about 20 seconds once a GPU was available.

## Cleanup

- Remove only the unique scripts directory the test created on the network volume, then
  terminate the pod in a `finally` block so a failed assertion still cleans up.
- Confirm the pod is absent from the pod list afterwards, and write a JSON report with GPU,
  region, price, command size, hashes, elapsed time and the reported cost.
- Never terminate pods that the test did not create. The maintainer's long-running pods share
  the account.
