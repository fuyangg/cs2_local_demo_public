# CS2 Local Demo — 本地分析 MVP

面向 **Fedora / WSL2 Ubuntu 24.04 + uv + Python 3.12（x86_64）** 的最小原型。Demo、解析结果和下载的地图图片只保存在你自己的电脑；GitHub 保存代码、测试、依赖锁和说明。

默认运行 **dev（8501）**；**prod（8502）** 使用独立工作树和 Python 环境，只接受通过完整测试且已人工确认的发布标签。完整流程见 [dev/prod 开发与发布](docs/ENVIRONMENTS.md)。

现有用户更新后，日常入口为：

```bash
bash scripts/setup-dev.sh
uv run --locked cs2ta --download-maps
bash scripts/run-dev.sh
```

仓库管理员在已登录 GitHub CLI 的本机执行一次 `bash scripts/setup-github.sh` 启用远端 main 保护和 prod 审批；如果脚本报错，远端规则仍未完成，详见上面的流程文档。

## 当前功能

项目自身的数据处理与测试统一使用 **Polars**，Parquet 缓存格式继续兼容。解析器和 Streamlit 的间接 Pandas 依赖及适配边界见 [数据处理约定](docs/DATAFRAMES.md)。

新增开发入口：FastAPI + SQLite 持久化任务库与独立串行 worker，支持导入、任务重试、比赛/回合查询及按需轨迹准备。运行 `uv run --locked cs2ta-api` 和 `uv run --locked cs2ta-worker`；完整接口与未来云端执行边界见 [本地后端说明](docs/BACKEND.md)。Streamlit 使用方式保持不变。

- 直接解析本地 CS2 `.dem`，不需要安装 CSDM、PostgreSQL、Docker 或 CS2 游戏。
- 查看地图、完整回合索引、击杀事件统计。
- 按回合查看击杀、炸弹、烟雾、闪光、HE 和燃烧事件，导出事件 JSON。
- 浏览器端即时拖动时间轴，播放/暂停整回合，支持步进、倍速与循环。
- 同时叠加 1–20 个回合，按回合开始或冻结结束对齐，支持单人/多人筛选。
- 按游戏人物编号使用黄/紫/绿/蓝/橙配色，CT 实线、T 虚线；支持地图大小与全屏播放。
- 按队伍、T/CT 阵营和常规/加时筛选，通过回合勾选表快速多选。
- 自动按 Demo 地图选择 Dust2、Nuke 或 Mirage 雷达底图，将世界坐标投影到地图；Nuke 支持上下层切换。
- 用 Demo SHA-256、解析器版本及 Schema 版本缓存结果到本地 Parquet。

**范围说明：** 当前是数据与可视化闭环，尚未实现地图语义区域、开局阵型分类、道具影响面积、战术标签或官方计分板。地图投影使用配套 overview 坐标参数；旧 Demo 与所用底图版本可能存在细节差异。回合编号是录制顺序，可能包含刀局和重启。缺少起止事件的不完整回合不进入索引；不假设 64 Tick/s。

与架构草案的关系：本版用 `demoparser2` 直接读 Demo，先验证最短数据链路；以后可通过新增 CSDM Adapter 接入来源，而不让界面依赖其数据库表。

## Fedora 快速开始

安装 uv 后，在项目根目录执行：

```bash
uv python install 3.12
uv sync --locked
uv run --locked pytest -q
uv run --locked cs2ta --download-maps
bash scripts/run-dev.sh
```

已有旧版项目时先运行 `git pull --ff-only`。浏览器打开 `http://localhost:8501`，左侧默认使用“浏览文件”：从当前目录选择子文件夹，再选择 `.dem` 文件。提供“主目录”“下载”“上一级”快捷按钮；会优先打开 `~/Downloads/g2-vs-navi/`（如果存在），其次为 Downloads 或主目录。

文件选择器直接读取运行应用的本机文件，不通过网页上传或复制 Demo。也可以切换为“输入路径”，填写例如：

```text
/home/fuyang/Downloads/g2-vs-navi/g2-vs-natus-vincere-m2-dust2.dem
```

默认进入“回合回放”，按下面三步操作：

1. 在左侧选择录像，点击“打开比赛”。
2. 在回合表中直接勾选；也可以选择队伍、进攻 T / 防守 CT、常规 / 加时，再点击“勾选筛选结果”。该按钮替换当前选择；超过 20 个结果时，可明确选择“只选前 20 个”，不会自动截断。
3. 点击“查看这 N 个回合”，随后在播放器内播放、拖动、调速、调地图大小或全屏。“单看”只显示一名玩家，勾选可同时显示多人。

配色对应游戏中的人物编号：`0 黄 / 1 紫 / 2 绿 / 3 蓝 / 4 橙`。T 轨迹和标记边框为虚线，CT 为实线。编号缺失时显示灰色，不按昵称或玩家排序补编号。姓名默认隐藏以减少重叠，单看一人时自动显示；更多显示选项可在播放器的高级设置中打开。Nuke 可在播放器内切换上下层。

回合选择新增“经济 / 配枪”：手枪局、eco、force-buy、4k-buy、full-buy、double-awp 和经济未知，按所选队伍与阵营、阶段共同筛选。金额规则与缓存说明见 [经济分类](docs/ROUND_FILTERS.md#经济与配枪)。

玩家列表按战队分组，每组按游戏人物编号 **0、1、2、3、4** 排列，缺失编号放在最后；不再按昵称字母排序。点击“G2全队”等按钮可只选择该队，之后仍可逐人勾选或单看。战队身份使用回合阵容元数据，换边后仍保持所属队伍；缺少战队名时显示“队伍 A/B”，身份也不明时归入“战队未知”。

“地图大小”支持小、标准、大；“全屏播放”将播放器和操作控件一起铺满屏幕，Esc 退出。浏览器若限制嵌入式全屏，可用“独立窗口”打开同一回放，再点击全屏。独立窗口保留当前位置、回合与玩家选择，不上传任何文件。

首次加载会解析尚未缓存的回合，多回合合并为一次解析。准备完成后，拖动、播放、玩家/回合筛选、楼层、缩放和平移均在浏览器内完成，不逐帧请求 Python。采样精度、对齐方式放在“高级设置”；事件列表、原始位置和比赛统计也保留在可选区域，主界面优先用于播放。自动精度在超过 8 个回合时使用较稀疏采样，并用浏览器显示插值保持移动连续。

时间轴使用相对 Tick，默认从各回合冻结结束对齐，也可在高级设置选择回合开始。缺少冻结结束元数据时显式标注回退。高级设置提供独立的“玩家轨迹保留”和“道具保留”选项，默认均为 10 秒，可选 2/5/10/20/30/60 秒或“一直保留（最长）”。秒数按可调的播放基准 Tick/秒换算，与倍速无关；玩家仅显示最近所选时长的轨迹，短回合结束后也会随叠加时间轴清除过期轨迹。选择一直保留可比较完整轨迹。隐藏已结束回合的玩家标记并标记“已结束”。播放器里的 64 Tick/秒是可调播放基准，不是对 Demo Tick 频率的推断；1× 速度也不承诺与真实比赛时间完全一致。平滑插值仅用于显示，不修改原始数据，不跨越缺失、死亡或楼层变化。一次最多加载 20 个回合、100 万条位置，超出时可增加采样间隔。

地图下载失败时可切换到“世界坐标”继续回放；数据缓存和地图缓存仍保留在本地。

地图资源首次下载约 2.9 MB，并校验 SHA-256；缓存到当前环境的地图目录，例如 dev 的 `.cache/maps/dev/2000935/`，之后无需联网。旧目录不删除；dev/prod 新环境各自按需生成缓存。资源来源、坐标约定和版本见 [地图资源说明](docs/MAP_ASSETS.md)。

Fedora 的 Demo 已在 Linux 本地时直接读取即可，不需要使用下面的 WSL 复制流程。

## 道具追踪

默认勾选“加载道具轨迹与落点”，点击“查看这 N 个回合”时会首次解析道具并缓存。播放器的“道具追踪”支持：

- 随时间轴查看实测飞行虚线、移动道具标记与爆开位置，使用 S（烟雾）、F（闪光）、G（手雷）、M（火）标记，另保留有轨迹的诱饵。
- 按道具种类筛选；默认跟随玩家/全队选择，也可取消勾选以看所有人的道具。未知投掷者单独保留。
- 在道具事件列表查看投掷者、相对 Tick、爆开 XYZ；点击条目跳转。无爆开事件时明确显示“最后观测（未确认落点）”。
- 切换“本回合落点总览（含未来事件）”比较整回合的落点；Nuke 仍按上下层过滤。
- 显示可关闭的估算范围：烟雾 144、HE 350、燃烧 180 世界单位；闪光 500 仅为定位示意，不代表致盲范围。圆形不模拟墙体遮挡、烟雾形变、火焰铺展或实际伤害。外圈从爆开开始固定显示：G 手雷和 F 闪光 3 秒、S 烟雾 15 秒、M 火 5 秒；秒数按播放器 Tick/秒基准换算，与倍速无关，属于显示时长而非真实作用时长。示意圈结束后按独立的道具保留时长（默认 10 秒）清除标记与整条飞行轨迹，无爆开事件则以最后观测为起点。选择“一直保留”可保留所有历史道具；整回合总览模式始终显示所有落点。

轨迹来自解析器的道具实体观测，不从玩家位置画推测抛物线；缺失轨迹不补齐。道具使用独立 `utility-tracks-v1.parquet` 缓存，玩家位置新增战队名后使用 `positions-v4-*`；旧缓存均保留。首次升级需重新点击加载。用户的 G2/NAVI Demo 仍需在其所在电脑上验收；云端验证使用公开 Mirage 测试 Demo。

## 旧版本升级

直接 `git pull --ff-only`，然后 `uv sync --locked` 并重启服务、重新打开比赛。新版本会补充队伍和加时信息；首次查看回合时，会生成包含人物编号的新位置缓存。旧 Demo、原始解析数据和旧位置缓存均保留，无需手动删除。

队伍 A/B 按首个非预热有效回合的两组 Steam ID 阵容固定，换边后保持身份；有战队名称时显示实际名称。加时状态读取 Demo 的明确游戏字段，不假设 MR12/MR15 或用录制回合序号猜测。未知信息显示为未知，不能匹配“常规”或“加时”过滤。详细约定见 [队伍、编号与回合筛选](docs/ROUND_FILTERS.md)。

## 1. 准备 WSL

若尚未安装 WSL，在**管理员 PowerShell** 执行：

```powershell
wsl --install -d Ubuntu-24.04
```

按提示重启、首次打开 Ubuntu 并创建 Linux 用户。之后以下命令都在 **Ubuntu / WSL 终端**执行。

```bash
sudo apt update
sudo apt install -y curl ca-certificates git unzip util-linux
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
uname -m
```

Python 由 uv 根据 `.python-version` 管理（3.12），无需手动安装 Python 或创建虚拟环境。建议输出架构 `x86_64`。其他架构的解析器 wheel 可用性未验证。不需要 Node.js、CUDA 或 GPU。

## 2. 把代码放到 WSL

建议代码放在 `~/projects/cs2_local_demo`，Demo 放在 `~/cs2-demos/`，避免大量小文件和解析读取跨 Windows 文件系统。

如果从本次交付的 ZIP 获取代码，先在 Windows 下载，再将下面路径中的用户名改成自己的：

```bash
mkdir -p ~/projects
unzip /mnt/c/Users/你的Windows用户名/Downloads/cs2_local_demo.zip -d ~/projects
cd ~/projects/cs2_local_demo
```

也可以直接从 GitHub 获取：

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/fuyangg/cs2_local_demo_public.git cs2_local_demo
cd cs2_local_demo
```

## 3. 安装与启动

在项目根目录执行：

```bash
bash scripts/setup-wsl.sh
```

安装脚本执行 `uv sync --locked`，由 uv 准备 Python、建立项目 `.venv`、按 `uv.lock` 安装依赖，然后用 `uv run --locked pytest -q` 运行测试。无需激活虚拟环境或执行 pip。原版的 `.venv` 可以由 uv 继续同步；不必删除本地 Demo 或缓存。

安装完成后，在同一项目目录下载地图底图，再启动应用：

```bash
uv run --locked cs2ta --download-maps
bash scripts/run-dev.sh
```

地图资源约 2.9 MB，包含 Dust2、Mirage、Nuke 上层和下层。dev 默认保存到 `.cache/maps/dev/2000935/`，不放在 Demo 目录中；已有完整缓存时会直接复用。下载成功会输出四张 PNG 的完整路径，也可执行 `ls -lh .cache/maps/dev/2000935/` 检查。更多地图来源、手动下载与自定义缓存说明见 [地图资源说明](docs/MAP_ASSETS.md)。如果应用已启动，先按 `Ctrl+C` 停止，再运行下载命令并重新启动。

启动后，在 Windows 浏览器打开：

```text
http://localhost:8501
```

启动脚本只监听 `127.0.0.1`，并关闭 Streamlit 使用统计。Demo 不上传到远端服务；首次安装需要下载 Python 依赖。按 `Ctrl+C` 停止服务。

## 4. 使用本次测试 Demo

将用户提供的原始文件放到电脑的 Downloads 中，然后在 WSL 执行：

```bash
uv run --locked python scripts/copy-demo.py \
  "/mnt/c/Users/你的Windows用户名/Downloads/g161-20261018123955719768926_de_dust2.dem"
```

脚本默认复制到 `~/cs2-demos/`，通过 SHA-256 校验后输出完整路径。不会删除 Windows 原文件或覆盖内容不同的同名文件；目标不能在 `/mnt` 或 Windows/网络挂载上。复制脚本需在你自己的 WSL 执行，云端会话不能代为操作电脑。

复制完成后启动页面：

```bash
bash scripts/run-wsl.sh
```

浏览器打开 `http://localhost:8501`。在左侧点击“主目录”，进入 `cs2-demos` 文件夹并选择比赛 Demo；也可切换“输入路径”，粘贴复制脚本输出的完整路径。点击「打开比赛」，随后进入「回合回放」，勾选要播放的回合，点击「查看这 N 个回合」，再在播放器内拖动或播放。已有位置缓存会复用。

本流程使用 WSL 本地副本进行分析，不从 Windows 挂载路径直接解析。源路径中有空格时，在终端用引号包住；Windows 用户名需要替换成真实用户名。

CLI 验证示例：

```bash
uv run --locked cs2ta "$HOME/cs2-demos/g161-20261018123955719768926_de_dust2.dem" --round 1
```

成功应输出地图、回合数、事件数、该回合位置行数与玩家数。具体数值需解析真实附件后确认，不预填结果。

## 5. 只上传代码到 GitHub

`.gitignore` 已排除 `.dem`、压缩包、Parquet、CSV、`data/`、`reports/`、`.venv/` 和本地配置。Demo 建议始终保存在仓库外。

仓库地址：<https://github.com/fuyangg/cs2_local_demo_public>。已有旧版克隆时，在项目目录更新远程并拉取：

```bash
git remote set-url origin https://github.com/fuyangg/cs2_local_demo_public.git
git pull --ff-only
bash scripts/setup-wsl.sh
```

提交文件包括 `.python-version`、`pyproject.toml` 和 `uv.lock`。`.venv/`、uv 下载缓存、Demo 和解析数据不提交。GitHub Actions 与 WSL 使用相同的 `uv sync --locked` / `uv run --locked` 流程。

`.gitignore` 不会清除已经被 Git 跟踪的文件，因此上传前必须检查暂存文件。不要使用 `git add -f` 添加 Demo。GitHub Actions 仅运行合成小样本测试，不下载或解析你的 Demo。

## 6. 本地数据组织

```text
app.py                         中文 Streamlit 页面
src/cs2ta/analysis.py           回合切分、击杀汇总、状态校验
src/cs2ta/pipeline.py           Demo 解析、版本缓存、Parquet
src/cs2ta/cli.py                命令行验收入口
uv.lock                        uv 跨平台依赖锁定文件
.python-version                uv 使用的 Python 版本
scripts/setup-wsl.sh            uv 环境同步和自动化测试
scripts/copy-demo.py            复制 Demo 到 WSL 本地并校验
scripts/run-wsl.sh              仅本机监听的启动入口
src/cs2ta/radar.py              地图资源下载、坐标投影、楼层与轨迹绘图
docs/MAP_ASSETS.md              地图来源、资源版本和投影约定
tests/                         小型合成输入测试（不含 Demo）
data/dev/<checksum-version>/   dev 数据，运行后生成，不提交
data/prod/<checksum-version>/  prod 数据，在独立工作树中生成，不提交
```

`manifest.json` 在事件和回合写入成功后才发布；失败解析可以重试。位置按回合和采样间隔缓存，读取时不填补缺失玩家。缓存只用于本项目；不是最终架构的完整 Canonical Schema。

## 7. 验证状态与本地验收

本次附件超过执行环境的 32 MiB 文件传入限制，无法在云端下载到执行工作区，**尚未用该 Dust2 文件完成验收**。开发验证使用解析器上游仓库自带的公开测试 Demo；具体记录见 `VALIDATION.md`。公开测试数据不随代码交付。

在你本地用指定 Demo 完成以下验收：

1. CLI 成功退出，并确认地图是 `de_dust2`。
2. 页面有完整回合列表和击杀事件；抽查一个回合的起止与击杀。
3. 提取该回合轨迹，检查玩家状态、队伍和坐标是否合理；缺失玩家不应被静默补齐。
4. 重启页面、重新解析同一个文件，结果应复用缓存。
5. `git status --short --untracked-files=all` 中不包含 Demo、缓存和虚拟环境。

这些验收尚需你在 WSL 执行。若出错，请提供终端错误与环境版本，不需要把 Demo 上传 GitHub。

## 常见问题

- **提示缺少 `round_start/round_end`**：先更新到最新代码。本版直接请求解析回合事件，支持事件枚举未列出的实体重建事件；如果实际仍未解析到回合边界，会输出事件计数和本地 `diagnostics.json` 路径。请提供该诊断内容，不要据此直接认定 Demo 损坏。缓存版本已更新为 `mvp-2`，无需删除旧数据。
- **`uv: command not found`**：执行 `source "$HOME/.local/bin/env"` 或重新打开 WSL 终端，再运行安装脚本。
- **安装下载失败**：检查 WSL 网络与代理；uv 首次可能下载 Python 和依赖。不要通过手动 pip 安装绕开 `uv.lock`。
- **复制失败**：确认源文件已完整下载。若目标已有不同内容的同名文件，可指定 `--destination-dir ~/cs2-demos/another-match`。
- **找不到文件**：Windows 的 `C:\\Users\\...` 在 WSL 中应写成 `/mnt/c/Users/...`；确认文件名和大小写。
- **解析很慢/内存不足**：先将 Demo 复制到 WSL 的 `~/cs2-demos/`，只提取一个回合，增大采样 Tick 间隔。资源需求取决于 Demo，未承诺固定内存或耗时。
- **不支持的 Demo/解析器错误**：确认是已解压的 CS2 `.dem`；新游戏协议可能需要升级解析器并重新验证。不要把解析失败解释为空比赛。
- **端口占用**：停掉其他 Streamlit 进程，或手动运行 `uv run --locked streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false`。
- **Windows 浏览器无法打开**：保持 WSL 进程运行，确认终端没有报错，并在 WSL 用 `curl http://127.0.0.1:8501/_stcore/health` 检查。WSL 的 localhost 转发需处于正常状态。

## 依赖来源

底层解析复用 [LaihoE/demoparser](https://github.com/LaihoE/demoparser)，界面使用 Streamlit，图表使用 Plotly，地图资源来自 [awpy-data](https://github.com/pnxenopoulos/awpy-data) 的版本化发布。地图图片及第三方 Demo 不提交到本代码仓库。

## 浏览器交互测试

支持在测试同时生成小尺寸 PNG、播放 GIF、筛选操作 GIF 与 HTML 索引。执行 `uv run --locked --group browser pytest -q --visual-artifacts artifacts/browser`；CI 自动将结果上传为 `playwright-visual-evidence` 附件。真实 Demo 的可选录制及来源说明见 [截图与 GIF 验证](docs/VISUAL_TESTING.md)。

日常运行只需要 `uv sync --locked`。浏览器测试是独立可选依赖组，CI 会安装并运行；本地需要时执行：

```bash
uv sync --locked --group browser
uv run --locked --group browser playwright install chromium
uv run --locked --group browser pytest -q
```

若系统已有 Chromium/Google Chrome，测试优先使用现有浏览器。Linux 缺少浏览器运行库时需按 Playwright 提示安装系统依赖。测试覆盖鼠标尚未释放时即渲染、无网络交互、播放/暂停/结尾/循环、多回合和玩家筛选、Nuke 分层及插值断层规则。
