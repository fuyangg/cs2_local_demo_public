# dev / prod 开发与发布

## 分支与环境

保留一个长期分支 `main`，用于通过测试后的开发集成。日常功能在短期 feature/fix 分支开发，通过 PR 和自动测试进入 main。prod 不跟随 main，而是固定到完成自动测试及人工内容确认的版本标签。

为保留已有文件，不搬动当前仓库。推荐目录：

```text
~/projects/cs2_local_demo/          # 现有目录，作为 dev；main 或功能分支
  .venv/                          # uv 开发依赖
  data/dev/                       # 开发数据
  .cache/maps/dev/                 # 开发地图资源
  .release/                       # 本机候选测试和人工确认记录

~/projects/cs2_local_demo-prod/     # release install 创建的 Git worktree
  .venv/                          # 独立 uv 环境，不安装 dev/browser 测试依赖
  data/prod/                      # 独立稳定版数据
  .cache/maps/prod/                # 独立稳定版地图资源
  .runtime/deployment.json         # 当前稳定版本、SHA 与上一版本

~/Downloads/g2-vs-navi/            # 两环境只读使用原始 Demo
```

两工作树共用 Git 历史，但代码检出、Python 环境、配置、数据和地图缓存分开。旧 `data/`、`.cache/maps/2000935/` 不删除、不自动复制到 prod；新环境按需生成自己的缓存。

| 项目 | dev | prod |
|---|---|---|
| 代码 | main / 功能分支 | 已批准的附注标签，detached HEAD |
| 地址 | `http://localhost:8501` | `http://localhost:8502` |
| 更新 | PR 合入 main 后拉取 | 明确执行 release install |
| 文件监听 | 开发时可重载 | 禁用文件监听和自动重载 |
| 页面标记 | 开发预览 DEV + 分支/提交 | 稳定版 PROD + 已安装标签/提交 |

## 1. 启动开发环境

在 Fedora 的现有项目目录执行：

```bash
cd ~/projects/cs2_local_demo
git pull --ff-only
bash scripts/setup-dev.sh
bash scripts/run-dev.sh
```

`setup-dev.sh` 用 uv 同步环境并跑日常测试。浏览器测试为可选依赖组，发布准备和 CI 会完整运行；日常 setup 未安装该组时会跳过浏览器测试，这样的日常结果不能作为发布证据。

旧 `setup-wsl.sh` / `run-wsl.sh` 是 dev 脚本的兼容入口。终端 Ctrl+C 停止服务。启动脚本持有运行锁，安装脚本不会更新正在运行的 prod。

## 2. 启用 GitHub 保护（仓库管理员执行一次）

在已登录 GitHub CLI 的 Fedora 终端执行：

```bash
gh auth status
bash scripts/setup-github.sh
```

脚本配置：

- main 必须通过名为 `test` 的检查并通过 PR 合入；禁止强推、删除，管理员也受保护。
- main 的 PR 不强制另一人审批，适合单人开发；prod 的内容确认另行记录。
- 创建 `prod` GitHub Environment，以当前登录用户为审批人；单人仓库允许发起人自行审批，发布仍需明确点击确认。

配置文件是 `.github/main-protection.json`。脚本若因权限、套餐或 API 限制失败，会明确报错；不能将 CI 绿灯等同于已启用分支保护。当前云端会话访问 GitHub 管理 API 返回 Forbidden，所以这些远端规则须由你本机执行后确认。私有仓库能否启用相应保护取决于 GitHub 提供的功能；未成功启用时，本地发布门禁仍有效，但远程直接推 main 尚未被服务器阻止。

## 3. 日常改动

```bash
git switch main
git pull --ff-only
git switch -c feat/example

# 修改代码并在 dev 预览
uv sync --locked --group browser
uv run --locked --group browser playwright install chromium
uv run --locked --group browser pytest -q

git add <本次代码文件>
git commit -m "Describe the change"
git push -u origin feat/example
gh pr create --base main --fill
gh pr checks --watch
gh pr merge --squash --delete-branch

git switch main
git pull --ff-only
```

若系统已有 Chromium 或 Google Chrome，测试优先用它；首次运行可安装 Playwright Chromium。Fedora 缺少运行库时，按报错安装对应系统依赖，例如通过 dnf 安装 Chromium。

合入 main 只改变 dev，不触发本机 prod 更新。不在 prod 修改源码，不向 prod 复制开发缓存，不在 prod 执行 `git pull main`。

## 4. 准备候选版本

先完成 PR 合入并同步 main，然后在 dev 根目录执行：

```bash
bash scripts/release.sh prepare v0.2.0
```

这一步要求代码已提交且已进入 main；存在 `origin/main` 时还要求候选已经进入该远程跟踪分支。会运行 uv 锁定环境中的完整 Python 和真实浏览器测试，所有测试通过且零跳过才写入候选记录，并输出完整的 40 位提交 SHA。

记录位于 `.release/candidate-v0.2.0.json` 和 `.release/tests-v0.2.0.xml`，包含测试数量、报告哈希、依赖锁哈希、精确提交和时间。它们不提交 Git。重新 prepare 会撤销旧候选记录，即使这次测试失败也不会保留旧人工确认。

此时尚未创建发布标签，prod 不变化。在 dev 中确认这个精确提交的界面和内容，包括：

- 本地文件选择、Demo 解析及已有数据读取。
- 时间轴拖动、播放/暂停、单人/多人和需要的回合叠加。
- 人物编号配色、T/CT 线型、队伍/加时筛选、地图楼层。
- 地图尺寸、全屏与独立窗口。
- 若改过缓存格式，已提升格式版本并确认旧版本可继续使用自己的数据。

这是用户内容确认清单，自动测试不能代替。代码若改变，旧候选立即不可批准或发布，需要重新准备和确认。

## 5. 确认并发布标签

实际检查完后，将下面占位符替换为 prepare 输出的完整 SHA 和你真实的确认说明：

```bash
bash scripts/release.sh approve v0.2.0 \
  --commit "这里填写完整的40位SHA" \
  --reviewer "fuyang" \
  --notes "填写你实际确认的内容" \
  --confirm-preview

bash scripts/release.sh publish v0.2.0 --push
```

approve 只记录确认；publish 验证测试证据和人工确认后才创建附注标签并推送。不同 SHA、未确认、失败/跳过测试、修改过的证据或同名标签都不能被覆盖发布。标签携带审计记录；这是流程门禁，不是独立身份认证或防仓库所有者手工改写的签名系统。

也可以在 GitHub **Actions → Reviewed production release → Run workflow** 中选择 main，填写版本、已在 dev 预览的完整 SHA、确认说明并勾选确认。工作流重新测试该精确 main 提交，通过 prod Environment 审批后创建标签、保存测试证据。若未配置环境审批，手动派发和显式内容确认仍在工作流中，但不要将它当成已设置的独立审批人门禁。

标签发布只让版本可供部署，不会自动连接或修改你的 Fedora。

## 6. 首次安装 prod

在 dev 根目录执行：

```bash
bash scripts/release.sh install v0.2.0
cd ../cs2_local_demo-prod
bash scripts/run-prod.sh
```

然后打开 `http://localhost:8502`。dev 可以继续在 8501 运行。安装器只接受带测试与人工确认记录的标签，创建独立 worktree，并执行 `uv sync --locked --no-dev`。

可以显式指定独立同级目录：

```bash
bash scripts/release.sh install v0.2.0 --prod-dir "$HOME/projects/cs2-stable"
```

安装器不会覆盖 dev、嵌套 dev 或修改其他仓库目录；不会自动清理未提交代码。成功后才写入部署记录；启动还会验证标签、SHA、依赖锁、detached HEAD 和源码清洁状态。已经部署的目录会自动识别为 prod，也不能被开发启动脚本误用。

## 7. 升级与回退

新版仍按 prepare → dev 内容确认 → approve → publish 流程。停止 prod 终端中的服务后，在 dev 执行：

```bash
bash scripts/release.sh install v0.2.1
```

再回 prod 目录启动服务。正在运行或源码有改动的 prod 会拒绝更新。安装依赖失败时，已有检出尽量恢复，旧部署记录保留，但留下失败标记并阻止启动，直到重新成功安装已知版本；避免把不完整依赖环境标成可用。

回退到上一个已安装且批准的版本，同样先停止 prod，再从 dev 执行：

```bash
bash scripts/release.sh rollback
```

也可明确安装某个历史批准标签。回退只切代码和对应 uv 依赖，不删除 Demo 或缓存。当前版本与上一版本记录在 `.runtime/deployment.json`，部署历史记录在 `.runtime/deployments.json`。

## 配置

共同默认值在 `config/environments.toml`。需要改端口或本地路径时，在相应工作树创建 `config/local.dev.toml` 或 `config/local.prod.toml`：

```toml
port = 8601
data_dir = "data/my-dev"
map_cache_dir = ".cache/maps/my-dev"
```

本地文件不进 Git。只读原始 Demo 可以共用路径，解析输出与地图缓存保持隔离。默认只监听 `127.0.0.1`。

本次引入的是发布机制；不会自动为尚未由用户确认的代码创建首个稳定版。首次 prod 仍需完成上述候选确认步骤。
