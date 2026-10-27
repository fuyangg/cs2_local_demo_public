# Playwright 截图与 GIF 验证

浏览器测试可在断言通过的操作节点保存 PNG，并将真实浏览器截图合成为 GIF。测试失败时自动保留现场截图；每个文件在 HTML 索引和 JSON 记录中标注对应测试、结果和数据来源。

## 本地生成

```bash
uv sync --locked --group browser
uv run --locked --group browser playwright install chromium
uv run --locked --group browser pytest -q --visual-artifacts artifacts/browser
```

完成后，终端输出本次 `index.html` 的绝对路径。每次运行创建新的 `run-<UTC 时间>-<随机后缀>/`，保留之前的记录；直接用浏览器打开索引查看所有图片和 GIF。

不传 `--visual-artifacts` 时仍执行交互断言，但不额外录制。生成文件位于已忽略的 `artifacts/`，不提交 Git。

主要演示内容：

- 两回合对齐叠加与时间轴。
- 8× 实际播放的连续截图 GIF，时长按照实际截图间隔编码。
- 全员、单人、单回合和道具总览的操作检查点 GIF；每帧有对应断言，停留时间用于阅读，并非操作录像的真实时间。
- 全队选择、道具事件跳转、全屏等原有测试的关键截图。
- 七张服役地图逐张展示及 GIF：核对底图加载、世界坐标投影与玩家显示。轨迹始终是合成测试数据；底图来源单独记录。
- 失败现场，包括断言失败前已生成的文件；索引会标明测试未通过。

图片最大 960 像素宽。`manifest.json` 记录代码 commit、工作区是否有改动、测试退出码、来源、GIF 帧数和采样 Tick。画面用于辅助人工复核，断言决定测试是否通过。

本地需要展示真实底图时，先运行 `uv run --locked cs2ta --download-maps`，再给测试添加 `--visual-map-cache .cache/maps/dev`；未指定底图缓存时，地图回归测试使用明确标注的占位图片。测试本身不联网下载。

## 使用真实公开 Demo

默认 CI 使用明确标注的合成轨迹，单独下载并校验七张地图的真实雷达资源，不自动下载 Demo。希望验证真实轨迹时，先显式准备上游固定版本的公开 Mirage 样例：

```bash
uv run --locked python scripts/prepare-visual-demo.py
```

脚本只下载固定 URL，校验 Demo SHA-256，复用既有解析缓存，并下载/校验现有版本的地图资源。原始文件与缓存保存在 `artifacts/visual-input/`，不上传到测试附件。来源与此前验证一致，见 `VALIDATION.md`。

脚本输出 `Dataset` 和 `Maps` 路径，然后使用它们：

```bash
uv run --locked --group browser pytest -q \
  --visual-artifacts artifacts/browser-real \
  --visual-dataset /absolute/path/to/dataset \
  --visual-map-cache /absolute/path/to/maps
```

也可以设置 `CS2TA_VISUAL_DATASET` 和 `CS2TA_VISUAL_MAP_CACHE`。仅演示测试使用该真实数据集；其他合成回归测试仍独立运行，索引逐项区分。真实数据集必须已有第 1、2 回合的 step16 位置缓存与道具缓存，测试本身不会解析 Demo 或联网获取资源。

## GitHub Actions

每次 push/PR 的 `Code tests` 工作流都会生成图片/GIF，并上传 `playwright-visual-evidence` 附件，保存 14 天。进入对应 Actions 运行页面，在 Artifacts 下载 ZIP，解压后打开其中的 `index.html`。

需要真实 Demo 的证据时，在 Actions 中手动运行 `Code tests`，勾选 `public_demo`。该运行会先准备固定公开样例，再运行完整测试并上传可视化附件；附件目录不包含原始录像或 Parquet。
