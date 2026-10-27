# Development and release workflow

- Use `uv` and the committed `uv.lock`; default environment is `dev`.
- Make future changes on short-lived feature/fix branches. Run Python and browser tests, push the branch, and merge through a passing PR into `main`.
- The initial commit introducing this workflow may bootstrap it onto `main` after local Python/browser tests pass; subsequent work follows the PR flow.
- Never edit a prod worktree, copy dev caches into prod, or run `git pull main` in prod. Production runs a reviewed annotated release tag in detached HEAD.
- Never invent a user's content approval. Prepare a candidate and identify its exact SHA; `approve`, release-tag publication and prod upgrade require explicit user confirmation of that candidate, or the user's own manual release commands.
- Use `bash scripts/setup-dev.sh`, `bash scripts/run-dev.sh`, and `uv run --locked --group browser pytest -q`. Browser tests require the optional `browser` dependency group and Chromium.
- Do not commit demos, caches, `.venv`, local config, `.release` receipts or `.runtime` deployment records.
- Version any incompatible cache/schema change and preserve prior cache files so production rollback does not consume incompatible data.
- GitHub main protection is configured by `scripts/setup-github.sh`. If the API is unavailable, report that server-side protection was not applied; do not claim CI checks alone prevent direct pushes.
