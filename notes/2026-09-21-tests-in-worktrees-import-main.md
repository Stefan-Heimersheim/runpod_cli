# Tests in a worktree import main's code

Observed 2026-09-21 while running the suite for `feature/remove-s3-api` in a temporary
`git worktree`.

- The devbox `.venv` installs `runpod_cli` as an editable package pointing at
  `~/runpod_cli/src`. Any Python started from that venv imports the main checkout's code,
  regardless of the current directory.
- In a worktree this fails loudly only when the branch adds a symbol main lacks
  (`ImportError: cannot import name ...`). When the API surface matches, the tests
  silently run against the wrong code and pass.
- Fix: run `PYTHONPATH=src python -m pytest` from the worktree root, or check the branch out
  in the main checkout instead.
