# Pattern Library

`books.jsonl` 登记已经完成单书蒸馏的作品。`patterns.jsonl` 保存至少由两本书支持的可迁移模式。证据不足的判断放入 `hypotheses/`。

不要把章节原文复制到本目录。证据使用书籍 ID 与报告路径引用。

写入模式后运行 `python3 -m novel_analysis validate-library`。同一 book ID 的多条证据不能替代两个独立来源。
