# Data Contracts

项目 Schema 位于 `schemas/`。JSONL 文件每行必须是一个完整 JSON 对象，不使用 Markdown 代码围栏。

## Chapter fact

`schemas/chapter_fact.schema.json` 保存可由原文直接支持的信息。

- `events` 记录发生了什么。
- `information_reveals` 记录谁获得了什么信息。
- `state_changes` 只记录发生变化的字段，不保存每章完整快照。
- `clue_candidates` 表示可能有后续意义的细节，不能直接声明为伏笔。
- `unknowns` 明确记录无法可靠判断的问题。
- `evidence.quote` 使用精确短引文，`occurrence` 表示它在本章第几次出现。
- `extractor` 必须记录 `novel-extractor`、Prompt 版本、模型和当前 run ID。缺失或 run 不匹配会阻止最终封存。

事件 ID 使用 `C000137-E001`，信息 ID 使用 `C000137-I001`，状态变化使用 `C000137-S001`，线索候选使用 `C000137-L001`。

## Chapter annotation

`schemas/chapter_annotation.schema.json` 保存结构解释。它必须引用事实中的 event ID，不能把推测写回事实文件。

Annotation 分片与 fact 分片使用相同章节范围，保存于 `facts/chapter_annotations/`。只有 `novel-linker` 写 annotation、实体表和 ledger。

爽点、payoff、reward 和 next opportunity 分开记录。`hook.strength` 使用 0 至 3，0 表示无有效钩子。

## Entity registry

`schemas/entity.schema.json` 定义稳定实体。角色、地点、势力、物品、能力和概念使用不同前缀。名字相同不等于同一实体，名字改变也不等于新实体。

## Ledgers

`schemas/ledger_event.schema.json` 同时约束 state、thread 和 clue 事件。账本只能追加新事件。更正旧结论时追加 `invalidate`，不要无痕覆盖历史。

每个 state change 必须被 state ledger 引用，每个 clue candidate 必须被 clue ledger 引用。facts 中非空的 entity ID 必须存在于 `index/entities.jsonl`。

## Arc analysis

`schemas/arc_analysis.schema.json` 允许多个层级和父弧。`craft_hypotheses` 是带证据和替代解释的分析假设，不是事实。

## Cross-book patterns

`schemas/cross_book_pattern.schema.json` 要求至少两个来源作品。`originality_constraints` 明确禁止搬运专名、标志性设定、原句和独特事件顺序。

单书完成后使用 `finalize-book` 生成全量产物哈希，再使用 `register-book` 登记。Pattern 只能引用 completion manifest 已封存的证据文件。

## Missing values

- 原文明确否定时使用 `false`。
- 原文没有提供信息时使用 `null` 或写入 `unknowns`。
- 字段对当前对象不适用时省略可选字段。
- 不用空字符串代替未知值。
