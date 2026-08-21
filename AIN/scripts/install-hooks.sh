#!/bin/bash
# 安装 git hooks（将 scripts/hooks/ 同步到 .git/hooks/）
# 用法：bash scripts/install-hooks.sh
# D-070：audit.py 已接入 pre-commit，提交前自动校验，ERROR 阻断。

set -e
cd "$(dirname "$0")/.."

mkdir -p .git/hooks
installed=0
for f in scripts/hooks/*; do
    [ -f "$f" ] || continue
    cp "$f" ".git/hooks/$(basename "$f")"
    chmod +x ".git/hooks/$(basename "$f")"
    echo "已安装: .git/hooks/$(basename "$f")"
    installed=1
done

if [ $installed -eq 1 ]; then
    echo "git hooks 安装完成（pre-commit 将自动运行 scripts/audit.py）。"
fi
