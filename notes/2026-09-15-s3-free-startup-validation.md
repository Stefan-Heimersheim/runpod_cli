# S3-free startup validation

Tested on 2026-09-15 from `feature/remove-s3-api`, based on main commit `f331c28`.
The implementation adapts the base64 approach from `119996a` on
`origin/remove-s3-api` to the current REST v2 creation request and all five scripts.

## Live result

- GPU: NVIDIA RTX 2000 Ada Generation, EU-RO-1, $0.24/hour reported by RunPod.
- Pod: `p1ubvt5qf37aad`.
- Startup command: 14,330 bytes, accepted by the current API.
- All five remote scripts matched locally generated SHA-256 hashes:
  `setup_root.sh`, `setup_user.sh`, `install.sh`, `start_pod.sh`, `terminate_pod.sh`.
- Fast setup completed, `nvidia-smi` identified the GPU, and the custom bashrc line
  preserved quotes, dollar signs, backticks, and Unicode.
- Test duration including creation and cleanup: 19.2 seconds. At the reported
  hourly rate this is roughly $0.0013; this is an estimate, not an invoice total.
- The test removed its unique script directory and terminated its pod. A subsequent
  pod-list request confirmed the pod was absent.

The test checks transfer and fast setup; it does not wait for all package installs
or the scheduled self-termination timer.

## Repeat the live test

The throwaway script used for this run has been deleted. What it did, and how to
reproduce it safely, is recorded in the
[live pod smoke test recipe](2026-09-18-live-pod-smoke-test-recipe.md).

## Local coverage

All 90 tests passed, including in an isolated installation with neither boto3 nor
botocore installed. The suite covers byte-for-byte decoding of all generated scripts, shell syntax,
quoted paths, empty files, write failure before startup, runtime/termination
sequencing after a setup failure, distinct per-pod directories, and initialization
without S3 credentials. Existing setup, API, availability, and SSH-config tests
remain in the suite.

## Behavior changes

S3 uploads, directory cleanup, host-key polling, boto3, and S3 credential
requirements are removed. The `--update_known_hosts` flag is removed, matching the
old branch. SSH verifies new hosts on first connection. Old `.tmp_*` directories
remain on the volume until manually removed. The network volume itself is still
required for persistent state.
