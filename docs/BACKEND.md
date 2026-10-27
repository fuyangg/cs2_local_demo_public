# 本地后端与独立解析任务

这是开发阶段的新入口：FastAPI 接收任务和提供查询，SQLite 保存任务与比赛索引，独立 Python worker 串行执行解析。现有 Streamlit 入口继续使用原来的流程；本次没有迁移 Vue，也没有部署云服务。

后端查询、解析结果处理与 Parquet 读写使用 Polars。第三方解析器输出在适配层转换一次，详见 [数据处理约定](DATAFRAMES.md)。

## 安装与运行

在项目根目录执行 `bash scripts/setup-dev.sh`。然后打开两个终端：

```bash
# 终端 1：API，只监听 127.0.0.1:8000
CS2TA_ENV=dev uv run --locked cs2ta-api
```

```bash
# 终端 2：持续取任务，一次解析一项
CS2TA_ENV=dev uv run --locked cs2ta-worker
```

打开 <http://127.0.0.1:8000/docs> 可以直接提交请求和检查响应。API 不会自动启动 worker；只有 API 运行时，任务会保持排队。API 重启、页面刷新或关闭不影响独立 worker。

按需运行模式：

```bash
# 最多执行一个排队任务；队列为空则直接退出
CS2TA_ENV=dev uv run --locked cs2ta-worker --once
```

默认任务库是 `data/dev/backend-v1.sqlite3`。Parquet 继续写入原有内容寻址目录，复用 Streamlit/CLI 已有缓存；没有修改原缓存格式。自定义数据目录仍通过 `config/local.dev.toml` 的 `data_dir` 设置。API 和 worker 必须使用相同工作目录、环境及配置。

## 提交与查询

导入运行服务的电脑上的 Demo：

```bash
curl -X POST http://127.0.0.1:8000/api/imports \
  -H 'Content-Type: application/json' \
  -d '{"source_path":"/absolute/path/match.dem"}'
```

返回 HTTP 202，响应包含任务 `id` 和 `status`。接口只校验本地文件，不执行 Demo 解析。任务结果中的 `match_id` 是 Demo SHA-256，同一内容的重复导入会复用数据集，不重复创建比赛。

| 接口 | 用途 |
|---|---|
| `GET /health` | API 存活状态；不代表 worker 存活 |
| `POST /api/imports` | 创建导入任务 |
| `GET /api/jobs` | 任务列表，支持 `limit`（最多 100）和 `offset` |
| `GET /api/jobs/{id}` | 查询状态、当前阶段、尝试次数、结果与错误 |
| `POST /api/jobs/{id}/retry` | 显式重试失败或中断任务 |
| `GET /api/matches` | 持久化比赛列表，同样支持分页 |
| `GET /api/matches/{match_id}` | 比赛信息与解析 manifest |
| `GET /api/matches/{match_id}/rounds` | 回合索引与队伍、阵营、阶段、经济筛选 |
| `POST /api/matches/{match_id}/positions` | 创建选定回合的位置任务 |
| `POST /api/matches/{match_id}/utility` | 创建整场道具轨迹任务 |
| `GET /api/matches/{match_id}/playback` | 只读取已完成缓存，返回现有播放器的数据格式 |

回合筛选参数沿用现有规则：`team=team_a|team_b`、`side=all|2|3`、`phase=all|regulation|overtime`、`economy=all|pistol|eco|force-buy|4k-buy|full-buy|double-awp|unknown`。队伍 A/B 仍只在一场内有意义，跨比赛的战队身份映射不在本次范围。每条回合记录新增 `match_id`，使用 `(match_id, round_id)` 标识，避免跨比赛冲突。

位置任务请求体：

```json
{"round_ids": [1, 2], "tick_step": 16}
```

支持 1–20 个不同回合，`tick_step` 为 1–1024 的整数，多个回合仍合并为一次位置解析。任务成功后读取：

```text
GET /api/matches/{match_id}/playback?round_ids=1&round_ids=2&tick_step=16&alignment=freeze_end
```

设置 `include_utility=true` 可以附带道具。缺少所需缓存时返回 409，不在查询接口中临时解析；先提交对应任务再重试读取。已有地图图片会一起返回，未下载时使用世界坐标，不自动联网下载。单次最多 100 万条位置的限制保持不变。回放接口目前一次读取一场，尚未实现跨比赛播放器或对比组。

## 状态、串行与恢复

状态流为 `queued → running → succeeded / failed`。进度报告 `preparing`、`ingesting`、`positions`、`utility`、`publishing` 等真实阶段，不估算百分比。可选的队伍/经济解析诊断保存在结果 `diagnostics` 中，未知值沿用现有规则。

- SQLite 事务保证同一任务不会被重复领取；相同参数且源文件大小/修改时间未变的排队/运行任务会合并。完成后的再次提交会创建新任务，执行时复用内容缓存。
- 本地 worker 持有文件锁，同一数据库只允许一个 worker，锁会在进程退出后由操作系统释放。
- SIGINT/SIGTERM 会要求 worker 完成当前任务后退出；强制杀进程或机器断电可能留下 `running`。
- 下次 worker 启动、取得独占锁后，将遗留 `running` 标为 `interrupted`，等待显式重试。重试会增加尝试次数并复用已完成缓存，不承诺从解析器内部断点恢复。
- 失败任务记录错误，不创建不完整的比赛索引；成功结果与比赛、回合索引在一个短事务中更新。解析期间不持有 SQLite 事务。

`--once` 的退出码表示 worker 是否正常运行；任务解析失败记录在任务状态中。自动化调用应检查任务的 `status`。

## 为以后按需云端执行预留的边界

`backend/contracts.py` 定义版本化 `ParseRequest` / `ParseResult`；`backend/tasks.py` 的 `execute()` 不依赖 FastAPI 或 SQLite。`ArtifactStore` 隔离输入文件获取、数据集获取和结果发布，当前实现只支持本地 `file:///...` URI。

可以完全绕过任务库，直接执行一个 JSON 请求：

```json
{
  "version": "parse-task-v1",
  "kind": "ingest",
  "source_uri": "file:///absolute/path/match.dem"
}
```

将请求保存在 `work/request.json`，运行：

```bash
uv run --locked cs2ta-task \
  --request work/request.json \
  --result work/result.json \
  --data-dir data/dev
```

成功返回结果 JSON 和退出码 0；失败写入 `status=failed` 的结果并返回 1。该入口不创建 SQLite。对已有数据集执行位置/道具任务时需提供 `dataset_uri`，位置任务还需 `round_ids` 和可选 `tick_step`。

将来接对象存储时，新增适配器负责下载到临时磁盘、复用/生成缓存、上传结果，并在所有结果上传成功后发布完成清单。由主服务校验和接收结果、更新 SQLite；云端解析容器不直接连接本地 SQLite。还需增加远程派发、任务租约/超时、鉴权和上传策略；当前没有实现 Lambda、对象存储或远程调度。

## 访问范围

当前入口只面向个人本地开发：CLI 固定监听 loopback，校验 Host、拒绝跨源浏览器请求，不开放 CORS，也不提供公网用户认证。文件路径指向服务所在电脑。不要通过端口转发将此入口直接暴露公网；Vue 开发时可通过 Vite 同源代理访问 `/api`。生产标记或 `CS2TA_ENV=prod` 下，新入口会拒绝启动。

任务库包含本地路径和比赛索引，位于已忽略的 `data/`，不提交 Git。备份 SQLite 时使用其在线备份机制或停服务后备份，不能只复制运行中的主数据库文件而忽略 WAL。
