---
name: novel-analysis
description: Use whenever the user asks to 拆书、分析网文、导入 TXT 小说、提取章节事实、追踪人物状态或伏笔、识别剧情弧、生成 Book DNA、蒸馏写作规则、对比多本小说，或继续和校验既有拆书任务。对长篇小说和 400 万字以上项目也必须使用本 Skill，通过本地分层流水线避免整书总结。
compatibility: Requires Python 3.10+ and OpenCode file, bash, and task tools.
---

# Novel Analysis

把长篇小说处理成可追溯的事实、结构分析和可迁移创作规律。所有写入都留在 `ANALYSE` 项目内。

## Locate the workspace

从包含 `opencode.json` 与 `novel_analysis/` 的目录运行命令。不要在其他目录创建镜像工作区、临时项目或分析结果。

## Choose the requested mode

- 新书初始化或导入时，执行确定性导入流程。
- 用户要求试拆时，选取开篇、中段、后段和结尾的连续窗口。
- 用户要求全书拆解时，创建 run 并按待处理 job 续跑。
- 用户要求复核时，只执行落锚、验证和低置信度复查。
- 用户要求 Book DNA 时，确认剧情弧与专题报告已经通过关卡。
- 用户要求多书蒸馏时，至少读取两本已完成作品的 `distilled/`，不要读取整本原文。

## Read references progressively

1. 开始任何任务前读取 `references/workflow.md`。
2. 写 JSON 或 JSONL 前读取 `references/data-contracts.md` 和项目 `schemas/` 中对应 Schema。
3. 调度子 Agent 前读取 `references/prompts.md`。
4. 完成一个阶段前读取 `references/quality-gates.md`。
5. 进行多书对比时再读取 `references/cross-book.md`。

## Deterministic commands

```bash
python3 -m novel_analysis init <book_id> --title "书名" --author "作者"
python3 -m novel_analysis ingest <book_id> "/path/to/novel.txt"
python3 -m novel_analysis validate <book_id>
python3 -m novel_analysis plan <book_id> --batch-size 10
python3 -m novel_analysis materialize-inputs <book_id> <run_id>
python3 -m novel_analysis materialize-review-inputs <book_id> <run_id> [--part <part-file>]
python3 -m novel_analysis batch <book_id> --start 1 --end 10
python3 -m novel_analysis ground-facts <book_id>
python3 -m novel_analysis validate-facts <book_id>
python3 -m novel_analysis validate-facts <book_id> --require-complete
python3 -m novel_analysis validate-structure <book_id> --require-complete
python3 -m novel_analysis finalize-book <book_id>
python3 -m novel_analysis register-book <book_id>
python3 -m novel_analysis validate-library
python3 -m novel_analysis status <book_id>
```

书名可用中文，`book_id` 只用小写英文字母、数字和连字符。导入会复制并规范化原文，之后不直接修改 `source/original.txt`。

## Response requirements

当输入文件尚未提供时，给用户的执行方案仍要明确写出 `init -> ingest -> validate`，并使用占位符展示将执行的命令。试拆方案要说明连续窗口、单批章数、唯一输出分片、证据落锚和 facts 验证。

明确写出阶段停止条件。`ground-facts` 或 `validate-facts` 失败时，不进入实体关联、剧情弧或蒸馏。没有当前会话中的真实命令输出时，只能说明“将执行”或“需要执行”，不得声称命令已经运行或报告虚构错误。

## Stage order

严格按以下顺序推进。关卡失败时停留在当前阶段并修复数据。

```text
ingest
  -> extract facts
  -> ground evidence
  -> validate facts
  -> resolve entities and ledgers
  -> detect nested arcs
  -> analyze craft
  -> distill one book
  -> compare books
```

## Agent routing

- `novel-extractor` 每个任务处理一个唯一章节批次，只写对应 `facts/chapter_facts/part-*.jsonl`。
- `novel-validator` 运行落锚和校验，复核失败项，只写 run 报告或被明确分配的事实文件。
- `novel-linker` 按章节顺序生成 annotation，并以单写者方式维护实体注册表和三个 ledger。
- `novel-analyst` 读取已验证事实、账本和少量定点原文，写剧情弧与专题分析。
- `novel-distiller` 默认不读原文，读取分析报告生成 Book DNA、规则和跨书模式。

Orchestrator 先执行一次 `materialize-inputs`，再把每个输入包交给独立 Extractor。事实落锚并通过验证后执行 `materialize-review-inputs`，为 Validator 和 Linker 生成不会被长行截断的审阅包。link jobs 必须按章节顺序由一个 Linker 执行。`validate-structure --require-complete` 未通过时，不调度 Analyst。不要让并行 Agent 同时修改 `entities.jsonl`、任何 ledger 或同一份 Markdown。

## Evidence discipline

Extractor 为证据填写 `quote` 和 `occurrence`。`quote` 必须是本章中的短原句，长度不超过 200 字。程序负责补 `start` 与 `end`。

事实字段只陈述原文出现的事件、信息和变化。章节目的、冲突类型、情绪曲线、爽点、奖励与钩子属于 annotation。首次出现的可疑细节只能记为 `clue_candidate`，等待后文确认。

## Long-book controls

- 默认抽取批次为 5 至 10 章，遇到超长章节时缩小批次。
- 每完成 100 章进行一次完整事实校验和实体一致性检查。
- 每 20 至 50 章生成状态 checkpoint，保留中间 state events。
- 使用重叠窗口识别剧情弧，允许微型、中型和长型弧并存。
- 按章数、字数和场景数分别统计节奏，不用单一全书平均值。
- 每本书保留一个可续跑 run 的 `manifest.json` 与派生 job 进度，失败后只重跑受影响分片。需要新版 Schema、Prompt 或原文时使用新的 book ID，避免覆盖旧产物。

## Completion rule

没有新鲜的 CLI 验证结果时，不宣称事实抽取完成。结构关卡未通过时不分析 arc。`finalize-book` 未通过时不宣称单书蒸馏完成。只有 `register-book` 成功的作品可进入跨书模式，`validate-library` 未通过时不登记结果。
