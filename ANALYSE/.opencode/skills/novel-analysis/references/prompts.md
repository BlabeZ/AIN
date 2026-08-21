# Agent Prompts

这些约束已经写入项目 Agent。调度任务时仍要给出书籍 ID、run ID、章节范围、输入路径和唯一输出路径。

## Extractor task

```text
处理 <book_id> 的第 <start> 至 <end> 章。
运行 batch 命令读取原文与索引。
按 chapter_fact.schema.json 输出每章一行 JSONL 到 <output_path>。
只抽取事实。每条记录提供精确短引文和本章出现序号，不手填字符坐标。
无法确认的内容写入 unknowns，不解释作者意图。
不要修改任何其他文件。
```

## Validator task

```text
复核 <book_id> 的 <fact_path>。
针对被分配的 `part-*.jsonl` 运行带 `--part` 的 ground-facts 与 validate-facts，读取失败项对应章节原文。
检查事件是否真的发生、状态是否真的改变、引文是否支持结论、别名是否误并。
修正时保留最小事实，不补写结构评价。
把结果写入 <report_path>，只在必要时修改被分配的 fact_path。
```

## Analyst task

```text
分析 <book_id> 的 <chapter_range>。
只使用已验证 facts、annotations、ledgers 与必要的定点原文。
识别可重叠的 micro、medium 或 long arcs。
每个技法判断都给出事实 ID、替代解释和置信度。
爽点、兑现、奖励、下一机会和下一冲突分别分析。
写入唯一的 arc 或 analysis 文件。
```

## Linker task

```text
按章节顺序处理 <book_id> 的 <fact_path>。
确认 fact 分片已通过 validate-facts。
输出一章一行 annotation 到 <annotation_path>。
解析别名并更新 entities.jsonl，把 state change 和 clue candidate 接入对应 ledger。
账本只追加事件，身份不确定时不合并。
完成后运行 validate-structure；全书完成后加 --require-complete。
```

## Distiller task

```text
读取 <book_id> 的 arc 与专题报告，默认不读原文。
生成 Book DNA 和可迁移规则。
删除专名、具体事件顺序、标志性设定与原句痕迹。
区分高置信度规律、单书假设和反例。
输出到 <output_path>，不要修改上游事实。
```

## Parallel dispatch

一次并行调度的任务必须互相独立。给每个任务明确唯一输出文件。Extractor 可以并行；Linker、实体注册、账本合并、全书统计和跨书模式汇总采用单写者流程。
