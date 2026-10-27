# Polars 数据处理约定

项目自身的解析编排、回合切分、队伍识别、经济分类、事件统计、轨迹/道具组装、雷达投影、API、CLI、Streamlit 页面及测试统一使用 `polars.DataFrame` / `polars.Series`。

## 外部依赖边界

固定版本的 `demoparser2==0.42.0` 内部创建数据后会调用 `to_pandas()`，公开接口没有 Polars 返回选项。`parser_adapter.parser_frame()` 在每次解析返回后立即将它转换为 Polars；所有下游模块只接收 Polars，项目中只有这一处 `pl.from_pandas()`。不修改或 monkeypatch 上游解析器。

`streamlit==1.51.0` 也依赖 Pandas，并可能在表格组件内部转换数据。项目直接向组件传入 Polars，不在业务代码里转换回 Pandas。

因此项目已移除 Pandas **直接依赖和直接导入**，但 `uv tree` / `uv.lock` 中仍会看到解析器与 Streamlit 带来的间接依赖。这不等于安装环境或整个运行进程完全没有 Pandas；要消除上游内部使用，需要另外替换或改造这些组件。

## 数据规则

- 读写 Parquet 使用 Polars，读取时将浮点 NaN 统一为 null；数据库和 HTTP 结果通过 `to_dicts()` 得到 Python 原生值，JSON 缺失值为 `null`。
- Steam ID 在事件合并前、位置规范化后转换为字符串，避免混合空值或拼接造成浮点精度损失。适配器保留原始 UInt64，不先转为浮点。
- 回合索引的胜方统一为可空整数：`T` / `2` → `2`，`CT` / `3` → `3`；缺失或无法识别的胜方为 null。原始事件中的胜方字段保持解析器原值，不因字符串格式导致整场导入失败。
- 回合行在 Python 层使用 `row(..., named=True)` 的字典；列筛选、过滤、聚合与连接使用 Polars 接口。连接回合上下文时验证一对一关系，并保留左侧回合顺序。
- 空回合、队伍上下文、经济和道具缓存均有明确 Schema；未知数据不会自动变成零、非加时或 eco。
- 不可变变换使用 `with_columns()`、`filter()` 等生成新结果，测试使用 Polars 原生断言。

## 缓存兼容

此次迁移保留 `mvp-2`、`positions-v4-*`、`utility-tracks-v1.parquet` 和 `round-economy-v1.parquet` 的数据含义与文件命名。此前生成的 Parquet 可以直接读取，不删除或覆写旧缓存；已有完整缓存会直接复用。

使用固定公开 Mirage Demo 验证了旧缓存逐文件内容不变、同一缓存生成的完整回放 JSON 与旧实现一致，以及新解析的所有事件/回合/位置/道具事实一致。独立解析时，上游不保证同 Tick 事件或实体观测的原始行顺序，因此新旧事实比较按记录集合进行，事件仍完整保留。

`tests/test_polars_migration.py` 防止重新引入项目级 Pandas 导入或转换，检查 UInt64 身份、NaN/null、空 Schema 和旧 Arrow/Parquet 缓存的只读复用。
