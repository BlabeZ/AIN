暂不能做剧情弧分析。

复核顺序：

1. 运行 `python3 -m novel_analysis ground-facts demo-book`，确认第 101 到 110 章的每条证据都能落锚。
2. 落锚成功后运行 `python3 -m novel_analysis validate-facts demo-book`，要求返回 `valid: true`。
3. 定点复核置信度低于 0.75 的事实，以及身份、死亡、境界、资源、关系等关键状态变化；无原文支持的内容应删除、修正或降置信度。
4. 首次出现且尚未被后文确认的可疑细节只能标为 `clue_candidate`，不能当作已确认伏笔或线程闭合。
5. 只有事实验证通过、实体 ID 稳定且 state、thread、clue ledger 已更新后，才能进入剧情弧分析。

本次实际执行第 1 步时失败：`books/demo-book/source/original.txt` 不存在。因此未继续运行 `validate-facts`，并停留在事实验证阶段。请先恢复 `demo-book` 的已导入工作区，再按上述顺序复核。
