# Architecture

## Boundaries

确定性程序拥有编码、换行、章节边界、字符坐标、哈希、运行分批和证据落锚。模型不参与这些任务。

章节 facts 只保存源文本可支持的陈述。Linker 生成 annotation、解析实体并维护账本。structure gate 通过后，arc 与专题报告保存跨章推断。distilled 保存去情节化规律。

## Persistence

JSONL 是机器事实与事件的主存储格式。Markdown 用于需要上下文阅读的分析报告。原文与上游事实不因下游分析而改变。

每个 run 拥有不可变来源哈希、批次大小和 extract/link/analyze/distill job。Extraction 按输出文件并行；共享实体表和账本由单一 Linker 按章节顺序合并。

## Long-book scaling

一部 400 至 500 万字小说可能包含数千章。章节索引只保存偏移与哈希，不生成数千个永久原文章节副本。人工诊断可通过 `batch` 读取指定范围。

Run 开始时由 `materialize-inputs` 单次扫描原文和索引，生成有上限的 extraction 输入包。子 Agent 不直接执行 CLI，也不读取完整原文。

facts 按章节区间分片。剧情弧按独立文件保存。状态采用稀疏事件与周期 checkpoint。失败重跑只影响来源哈希、Prompt 或 Schema 发生变化的下游节点。

## Future extension points

- `novel_analysis/providers/` 可在确认模型供应商后增加 API 批处理适配器。
- `library/statistics/` 可增加 DuckDB 或 Parquet 派生索引，JSONL 继续作为可审计来源。
- `schemas/` 可通过版本目录支持迁移，旧 run 保留原 Schema 版本。
- `eval/gold/` 可为不同题材保存人工金标准。
- `analysis/style/` 可增加量化语言统计，不保存可识别的作者句式模板。

这些扩展点当前不引入依赖，也不改变现有文件契约。
