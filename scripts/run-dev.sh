#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ ! -f .runtime/deployment.json ]] || { echo '这是 prod 目录，请使用 scripts/run-prod.sh。' >&2; exit 1; }
exec uv run --locked python -m cs2ta.launch dev
