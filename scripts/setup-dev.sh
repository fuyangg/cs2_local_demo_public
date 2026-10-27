#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ ! -f .runtime/deployment.json ]] || { echo '这是 prod 目录，请回到 dev 安装开发环境。' >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo '请先安装 uv，参见 README。' >&2; exit 1; }
uv sync --locked
CS2TA_ENV=dev uv run --locked pytest -q
echo '开发环境就绪：bash scripts/run-dev.sh'
