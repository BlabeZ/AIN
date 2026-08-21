# Quality Gates

## Gate A

进入事实抽取前需要满足以下条件。

- `book.json` 存在且 source 状态为 ingested
- `validate` 返回 `valid: true`
- 章节数量与抽样标题经过人工或 Agent 快速检查

## Gate B

进入实体与线程关联前需要满足以下条件。

- 目标范围内每章恰好有一行 facts
- `ground-facts` 无错误
- `validate-facts` 返回 `valid: true`
- 关键状态和低置信度事实已经复核

建议试跑验收目标为证据支持率至少 98%，实体误并率低于 2%。这些是项目目标，需要根据首批人工金标准校准。

## Gate C

进入剧情弧分析前需要满足以下条件。

- 实体 ID 在目标范围内稳定
- state、thread 和 clue ledger 已更新
- 开放线程与已解决线程可以区分
- 弧边界允许重叠，不强求唯一划分
- `validate-structure <book_id> --require-complete` 返回 `valid: true`

## Gate D

进入 Book DNA 前需要满足以下条件。

- 开篇、中段、后段和结尾均有结构报告
- 爽点、奖励、钩子、节奏、人物行为和伏笔均有专题结果
- 技法判断包含证据、替代解释和置信度
- 全书平均值已经按阶段拆分复核
- `finalize-book <book_id>` 成功并生成 `distilled/completion.json`

## Gate E

登记跨书模式前需要满足以下条件。

- 至少两个来源作品支持该模式
- 模式已经去除专名、原句和具体情节顺序
- 已记录适用条件、变化形式和失败方式
- 原创性约束可直接用于新书检查
- `validate-library` 返回 `valid: true`
- 每本来源作品已经通过 `register-book`，其 completion manifest 与当前产物哈希一致

## Human gold set

每种新题材建立人工金标准。建议从不同阶段选取 30 至 50 章，人工确认事件、状态变化、身份合并、线索与钩子。模型或 Prompt 变更后先重跑金标准，再决定是否重跑全书。
