# Cross-book Distillation

## Inputs

跨书处理只读取每本书的 `distilled/`、arc 报告、统计结果和必要证据引用。默认不读取 `source/original.txt`。

## Registry

每本作品先运行 `finalize-book`，再运行 `register-book`。只有 completion manifest、来源哈希和分析产物仍一致的作品才会进入 `library/books.jsonl`。

模式写入 `library/patterns.jsonl`，遵守 `cross_book_pattern.schema.json`。`evidence_refs` 使用相对对应书籍目录的真实报告路径。只有单书来源的规律进入 `library/hypotheses/`，等待后续作品验证。

## Comparison dimensions

- 同类作品共有的剧情循环及长度范围
- 不同题材对同一机制的变体
- 作者独有技巧与跨书稳定模式的差别
- 模式在中后期失效或膨胀的条件
- 新书采用模式时必须改变的情境、资源和人物关系

## Originality boundary

保留机制，删除表现层。跨书模式不得保存专名、独有世界观术语、连续事件序列、标志性桥段或可识别原句。句式统计只保留比例与功能，不生成模仿某位作者的句子模板。
