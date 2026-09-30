# Guide for agents

## Context

`runpod_cli` (`rpc`) is a command-line tool for creating and managing RunPod GPU pods. It is
a fork of Apollo Research's `runpod_cli`, which is legacy and no longer maintained: all work
merges into `main` of this repo, never upstream. Read `README.md` for what the tool does and
how users run it. Source lives in `src/runpod_cli/`, unit tests in `tests/` (run `pytest`).

## Development

- One PR per change. Name the branch `fix/<slug>` for a bug fix and `feature/<slug>` for
  anything else. The maintainer merges; agents do not.
- Before branching, run `gh pr list` and look for an open PR. If there is one, branch from
  and target the newest open PR branch so the PRs form a stack that merges in order; branch
  from `main` only when nothing is open. Check `git branch --show-current` first: the
  checkout may not be on `main`.
- Never force-push. If a push is rejected, `git fetch` first: GitHub rewrites stacked branches
  after the PR below them merges.
- End every commit message with an attribution trailer matching the existing history, e.g.
  `Co-authored-by: Claude Fable 5.1 <noreply@anthropic.com>` or
  `Co-authored-by: Codex GPT-6 Astra <noreply@openai.com>`.
- Unit tests must pass before opening a PR. Tests that create real pods cost money: run them
  only when the task needs it, and ask the maintainer before creating a pod.

## Where to write things

- **`README.md` is for users.** Commands, flags, setup, and user-visible known issues. Nothing
  else goes there.
- **`notes/` is for everything else worth keeping**: investigations, incidents, hardware
  quirks, things that were tried and why they failed. One file per topic named
  `YYYY-MM-DD-short-slug.md`; add a line to `notes/README.md`. Write it so that a reader can
  tell what was observed, what was concluded, and what remains open.
- **Code comments stay short.** Minimal explanation when needed, no need to explain the
  incident log (that stays in notes/) or debugging narratives. Never add a comment that
  justifies a change or describes the bug it fixes: that belongs in the commit message
  and PR description.
