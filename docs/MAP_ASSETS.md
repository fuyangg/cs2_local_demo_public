# 地图底图与坐标约定

## 来源与本地缓存

雷达图片及 overview 元数据来自 [pnxenopoulos/awpy-data](https://github.com/pnxenopoulos/awpy-data) 的 [CS2 ClientVersion 2000935 发布](https://github.com/pnxenopoulos/awpy-data/releases/tag/2000935)。

- 坐标来源：该发布的 `map_data.json`，固定版本，不自动追随最新地图。
- 图片来源：该发布的 `images.zip`（2,876,805 字节）。
- 图片压缩包 SHA-256：`217dec0b1f2203cc7e9f7bb1b9ef13873665f093203a7df7231b9eb0074b649e`，与上游 `manifest.json` 一致。
- 一次准备七张服役地图 Ancient、Anubis、Cache、Dust2、Inferno、Mirage、Nuke，共八张 PNG（Nuke 有上下层）；只读取明确列出的 PNG，不解包任意路径。
- 缓存目录：由当前环境配置决定，dev 为 `.cache/maps/dev/2000935/`，prod 为 `.cache/maps/prod/2000935/`，均已被 Git 忽略。

下载入口：

```bash
uv run --locked cs2ta --download-maps
```

服役池按 2026-10-27 核对：[Valve 的 Season 5 公告](https://store.steampowered.com/news/posts/?appids=730&enddate=1783551090)确认 Cache 替换 Overpass；[Season 4 更新](https://store.steampowered.com/news/posts/?appids=730&enddate=1769036296&feed=steam_community_announcements)确认 Anubis 替换 Train。此下载器准备的是上述七张地图的二维雷达底图，不下载游戏服务器文件；未来地图池变化时需要更新支持列表与配套坐标。

旧版本仅下载四张底图的用户，更新代码后重新执行同一命令即可补齐。已有同版本底图内容保持一致，不需要删除缓存；八张图片齐全后再次执行不会联网。成功输出八个 PNG 路径。

也可以在页面点击“下载地图底图”。首次需要访问 GitHub Releases；下载失败保留世界坐标视图，不影响 Demo 数据。

无法由脚本联网下载时，可以用浏览器下载同一发布的 `images.zip`，将其中以下文件放入缓存目录：

```text
.cache/maps/dev/2000935/de_ancient.png
.cache/maps/dev/2000935/de_anubis.png
.cache/maps/dev/2000935/de_cache.png
.cache/maps/dev/2000935/de_dust2.png
.cache/maps/dev/2000935/de_inferno.png
.cache/maps/dev/2000935/de_mirage.png
.cache/maps/dev/2000935/de_nuke.png
.cache/maps/dev/2000935/de_nuke_lower.png
```

以上为 dev 示例，prod 应放在其独立工作树的 `.cache/maps/prod/2000935/`。

图片来自 Valve 游戏资源，上游代码的许可证不等于图片授权；参见 [awpy-data Licensing](https://github.com/pnxenopoulos/awpy-data#licensing)。本仓库不重新分发这些图片。

## 世界坐标到雷达像素

图片原始尺寸为 1024 × 1024，坐标原点在左上角，X 向右、Y 向下。

```text
pixel_x = (world_x - pos_x) / scale
pixel_y = (pos_y - world_y) / scale
```

| 地图 | pos_x | pos_y | scale |
|---|---:|---:|---:|
| Ancient | -2953 | 2164 | 5.0 |
| Anubis | -2796 | 3328 | 5.22 |
| Cache | -2000 | 3250 | 5.5 |
| Dust2 | -2476 | 3239 | 4.4 |
| Inferno | -2087 | 3870 | 4.9 |
| Mirage | -3230 | 1713 | 5.0 |
| Nuke | -3453 | 2887 | 7.0 |

这里使用完整静态雷达 PNG 的 overview 投影，保持图片与轨迹同比缩放。游戏内雷达的动态旋转、缩放不应用到此静态视图。Plotly Y 轴固定为 `[1024, 0]`，保持图片方向，拖动 Tick 时底图范围不漂移。原始 Parquet 坐标不修改；悬停显示原始 X/Y/Z。

## Nuke 楼层

来自 overview 的 `verticalsections`：下层 `-10000 ≤ z < -495`，上层 `-495 ≤ z < 10000`。边界 `-495` 属于上层，未知高度不自动归层。

上下层使用相同二维投影，切换各自 PNG，并按高度过滤玩家与路径。跨层、死亡和位置采样断层都作为轨迹断点，避免连接两个不可连续观察的点。楼梯和通风管道仍是基于高度的二维近似，不表示实际可达性或区域控制。

## 版本与验证

该资源来自 2026-10-23 生成、对应 CS2 1.41.9.2 的发布。Demo 可能录于更早的地图版本，底图细节不保证完全一致；本版尚未从 Demo 自动选择历史雷达。

投影原点、尺度与方向、Nuke 分层边界和跨层断线均有自动化测试。已用公开 Mirage Demo 渲染并检查真实位置/轨迹叠图；用户的 Dust2/Nuke Demo 尚需在本机与游戏回放抽查核对。未配置的地图保留世界坐标视图，不套用其他地图参数。
