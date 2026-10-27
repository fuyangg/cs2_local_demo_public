#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ ! -f .runtime/deployment.json ]] || { echo '请从 dev 目录运行发布、安装或回退命令。' >&2; exit 1; }
export CS2TA_ENV=dev
exec uv run --locked python -m cs2ta.release "$@"
