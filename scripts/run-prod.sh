#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo 'prod 尚未安装。请在 dev 目录通过 release install 安装已批准标签。' >&2
  exit 1
fi
# Production sync happens only during reviewed install, never on every launch.
exec uv run --no-sync python -m cs2ta.launch prod
