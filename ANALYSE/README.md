# ANALYSE

本目录是一套面向超长网文的本地拆书工作区。它将确定性文本处理、OpenCode 多 Agent 分析和多书蒸馏分开，避免把整本小说直接交给模型总结。

## Requirements

- Python 3.10 或更高版本
- `jsonschema`，可通过 `python3 -m pip install -e .` 安装
- OpenCode
- UTF-8、UTF-16 或 GB18030 编码的 TXT 小说

项目不写入 `ANALYSE` 之外的位置。导入源文件时只读取外部 TXT，并将规范化副本保存到对应书籍目录。模型供应商仍会接收被调度的章节批次，隐私边界取决于当前 OpenCode provider。

## Quick start

在本目录启动 OpenCode，修改配置后需要重启 OpenCode 才能加载 Skill、Agent 与命令。

```bash
python3 -m novel_analysis init example-book --title "示例小说" --author "作者"
python3 -m novel_analysis ingest example-book "/absolute/path/to/novel.txt"
python3 -m novel_analysis validate example-book
python3 -m novel_analysis plan example-book --batch-size 10
python3 -m novel_analysis materialize-inputs example-book run-id
python3 -m novel_analysis materialize-review-inputs example-book run-id --part part-000001-000010.jsonl
python3 -m novel_analysis validate-structure example-book --require-complete
python3 -m novel_analysis finalize-book example-book
python3 -m novel_analysis register-book example-book
python3 -m novel_analysis validate-library
python3 -m novel_analysis status example-book
```

也可以在 OpenCode 中使用以下命令。

```text
/novel-import example-book 示例小说 /absolute/path/to/novel.txt
/novel-analyze example-book 先做分层试拆
/novel-status example-book
```

## Project layout

```text
ANALYSE/
├── .opencode/
│   ├── agents/                Extractor, Validator, Linker, Analyst, Distiller
│   ├── commands/
│   └── skills/novel-analysis/
├── novel_analysis/           Python CLI
├── schemas/                  JSON Schema 数据契约
├── templates/                单书与跨书报告模板
├── books/                    每本小说的隔离工作区
├── library/                  多书模式库
├── evals/                    Skill 评测样例
└── tests/                    确定性程序测试
```

每本书在 `books/<book_id>/` 下拥有独立的 source、index、facts、ledgers、arcs、analysis、distilled、runs 和 eval 目录。增加新书不会覆盖已有书籍。

## Data flow

```text
TXT
  -> normalized source and chapter index
  -> grounded chapter facts
  -> annotations, entities, and append-only ledgers
  -> overlapping narrative arcs
  -> topic analyses and Book DNA
  -> cross-book transferable patterns
```

Extractor 只提交短引文。运行以下命令后，程序会把引文映射到规范化原文的全局字符坐标。

```bash
python3 -m novel_analysis ground-facts example-book
python3 -m novel_analysis validate-facts example-book
python3 -m novel_analysis validate-facts example-book --require-complete
```

普通验证允许只处理试拆范围。`--require-complete` 用于全书阶段，会报告尚未生成 facts 的章节。
日常批次应使用 `--part part-000001-000010.jsonl` 只处理当前分片，避免超长作品反复扫描全部 facts。

Linker 完成后运行 `validate-structure --require-complete`。该命令检查 annotation 覆盖、实体引用、state/clue ledger 回链及 arc 逻辑。未通过时不会解锁剧情弧分析。

`materialize-inputs` 一次读取原文和索引，为 extraction job 生成不超过 50 章、50 万字符的输入包。子 Agent 禁用 shell，读取与写入都需要确认具体路径。

事实分片完成落锚和验证后，`materialize-review-inputs` 会生成 `review-*.json`，把短分块原文与格式化事实绑定。Validator 和 Linker 读取该文件，避免 JSONL 单行超过文件工具显示上限；事实有修改时必须重新生成审阅包。

## Multi-book use

每本书完成单书蒸馏后，在 `library/books.jsonl` 登记。跨书规律进入 `library/patterns.jsonl`，只有一个来源的判断留在 `library/hypotheses/`。跨书模式至少需要两个不同 book ID 提供证据，并通过 `validate-library`。

`finalize-book` 会重跑上游关卡，要求有效 arcs、四份专题报告和完整 Book DNA，并生成带产物哈希的 `completion.json`。`register-book` 只接受这个已封存状态；同一本书重新封存后再次运行该命令，会在跨进程锁保护下原位刷新登记哈希。

出于安全考虑，所有 OpenCode Agent 均禁用 shell。Orchestrator 会给出需要执行的 CLI 命令，由你在本目录终端运行；子 Agent 只能读取和修改其角色允许的项目路径。

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 -m novel_analysis --help
```

CLI 使用 JSON Schema 强制校验 Agent 输出，并另外检查源文件哈希、章节偏移、事实重复、证据引文和跨文件引用。
