# Workflow

## 1. Initialize and ingest

确认书籍 ID、书名、作者和 TXT 路径。运行 `init`、`ingest`、`validate`。导入程序负责编码识别、换行规范化、章节索引、字符坐标与哈希。

章节标题无法稳定识别时，先检查 `index/chapters.jsonl`，不要继续调用模型。无标准章节标题的文本会被视为单章，通常需要人工确认。

## 2. Pilot before a full run

新类型小说或新版 Schema 应进行分层试拆。选取 3 至 4 个连续窗口，每个窗口 20 至 30 章，覆盖开篇、中段、后段和结尾。试拆用于发现字段缺失、别名冲突、证据难以落锚和成本异常。

Schema 已在同类文本上验证，且用户明确要求直接全书处理时，可以跳过试拆。

## 3. Plan extraction

运行 `plan` 创建 extract、link、analyze 和 distill job。job 文件保存不可变计划，运行 `status` 根据输出内容派生 completed、invalid、pending 和 blocked。只调度当前关卡允许且派生状态为 pending 的 job。

运行一次 `materialize-inputs <book_id> <run_id>`。它只读一次规范化原文和索引，为每个 extract job 写入受限输入包。章节正文保存在 `raw_text_chunks`，按数组顺序拼接即为完整原文；分块用于避免文件工具截断超长 JSON 行。Extractor 不直接读取 `source/original.txt`，也不运行 shell。

每个 job 只拥有一个输出文件。并发时不要共享输出路径。

## 4. Extract and ground facts

Extractor 通过 `batch` 获取原文与索引。输出符合 `chapter_fact.schema.json` 的 JSONL。每章一行，证据只填短引文和出现序号。

批次完成后对唯一分片运行 `ground-facts <book_id> --part <part-file>`。命令找不到引文时会失败并指出章节。修正引文后重试，禁止手填猜测坐标。全部分片结束后再运行一次不带 `--part` 的全书落锚关卡。

## 5. Validate facts

对当前分片运行 `validate-facts <book_id> --part <part-file>`。全书完成后运行 `validate-facts <book_id> --require-complete`。随后由 Validator 针对以下内容进行抽样或定点复核。
验证通过后运行 `materialize-review-inputs <book_id> <run_id> --part <part-file>`。它把完整原文分块和格式化事实绑定在同一审阅包中，避免 JSONL 长行被文件工具截断。Validator 修改事实后必须重新运行该命令。

- 低于 0.75 的置信度
- 身份、死亡、境界、资源和关系等关键状态
- 跨章线索、兑现与线程闭合
- 落锚失败或原文可能存在同句重复的证据

Validator 只删除、修正或降置信度，不补写无证据解释。

## 6. Resolve entities and ledgers

在章节事实稳定后，由 `novel-linker` 按 link job 的章节顺序更新实体注册表和 annotation。别名与称号指向稳定实体 ID。身份尚不能确认时保留未解析引用，不要提前合并。

状态、线程和线索采用事件账本。每 20 至 50 章生成 checkpoint。`unknown` 与 `false` 必须区分。

每完成约 100 章或一个 checkpoint 后运行 `validate-structure`。全部 link job 完成后运行 `validate-structure <book_id> --require-complete`。失败时停留在 link 阶段。不要在每个小分片后全库扫描。

## 7. Detect arcs

依据剧情问题的建立、升级和解决识别弧。官方分卷仅作参考。允许同一章属于多个弧。

- micro 通常覆盖 1 至 8 章
- medium 通常覆盖 15 至 60 章
- long 通常覆盖 80 至 300 章

先建立 thread，再用重叠窗口识别 arc。边界存在争议时记录 alternative interpretation 和置信度。

## 8. Analyze craft

Analyst 主要读取事实、annotation、ledger 和 arc。只有在判断依据不足时，才通过 `batch` 定点读取少量原文。

每项技法判断需要 claim、evidence、alternative explanation 和 confidence。作品成功不能自动证明每种安排有效。

## 9. Distill

Distiller 读取结构报告，不依赖整本原文。先生成单书 Book DNA，再进行去专名、去情节和去原句处理。

跨书模式必须由至少两个不同 book ID 支持。只有一本书出现的做法标为 single-book hypothesis。写入后运行 `validate-library`。
