#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
repo="${1:-fuyangg/cs2_local_demo_public}"
if [[ "$repo" != "fuyangg/cs2_local_demo_public" ]]; then
  echo '此配置脚本仅针对 fuyangg/cs2_local_demo_public。' >&2
  exit 1
fi
command -v gh >/dev/null 2>&1 || { echo '请安装 GitHub CLI 并运行 gh auth login。' >&2; exit 1; }
gh auth status
gh api --method PUT "repos/$repo/branches/main/protection" --input .github/main-protection.json >/dev/null
echo 'main 保护已启用：必须通过 test 检查和 PR，不允许强推或删除。'
reviewer_id="$(gh api user --jq .id)"
review_file="$(mktemp)"
trap 'rm -f "$review_file"' EXIT
cat > "$review_file" <<EOF
{
  "wait_timer": 0,
  "prevent_self_review": false,
  "reviewers": [{"type": "User", "id": $reviewer_id}],
  "deployment_branch_policy": {"protected_branches": true, "custom_branch_policies": false}
}
EOF
if ! gh api --method PUT "repos/$repo/environments/prod" --input "$review_file" >/dev/null; then
  echo 'main 已配置，但 prod Environment 审批未配置成功。请检查仓库套餐/管理权限；不能视为远程审批已启用。' >&2
  exit 1
fi
echo 'prod Environment 审批已配置。单人仓库允许发起人自行完成审批。'
