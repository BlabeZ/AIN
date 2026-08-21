#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《别查了，你们找的幕后人物全是我》一致性审计脚本
用法：在 AIN/ 目录下运行  python3 scripts/audit.py
定稿校验（AGENTS.md 防错机制）：输出必须 0 ERROR 才算章节定稿完成；WARN 项由作者人工确认。
检查项：
  1. 全部 state JSON 语法有效性
  2. canon 一致性：已写章节的细纲 canon_through 必须=章节号；正文/细纲存在；卷纲/master/弧纲不得落后
  3. planned 预置→当前事实扫描（identities/relationships/foreshadowing）
  4. 正文禁用词/禁用字符（词表随剧情推进维护）
  5. 细纲状态头
"""

import json
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = os.path.join(ROOT, 'Novel')

errors = []
warnings = []


def rel(p):
    return os.path.relpath(p, ROOT)


# ---------- 1. 全部 JSON 语法校验 ----------
json_files = glob.glob(os.path.join(N, 'state', '**', '*.json'), recursive=True)
for f in json_files:
    try:
        json.load(open(f, encoding='utf-8'))
    except Exception as e:
        errors.append(f'JSON 损坏: {rel(f)} -> {e}')

# ---------- 2. canon 一致性 ----------
cur = json.load(open(os.path.join(N, 'state', 'current.json'), encoding='utf-8'))
canon = cur.get('canon_through_chapter', 0)

for n in range(1, canon + 1):
    # 2a. 正文存在
    fp = os.path.join(N, f'chapters/{n:03d}.md')
    if not os.path.exists(fp):
        errors.append(f'正文缺失: chapters/{n:03d}.md')
    else:
        t = open(fp, encoding='utf-8').read()
        if '（正文待写）' in t:
            errors.append(f'正文仍是占位符: chapters/{n:03d}.md')
    # 2b. 细纲存在 + canon_through = 章节号
    fp = os.path.join(N, f'outline/chapters/{n:03d}.md')
    if not os.path.exists(fp):
        errors.append(f'细纲缺失: outline/chapters/{n:03d}.md（正文已到 ch{canon}）')
        continue
    s = open(fp, encoding='utf-8').read()
    m = re.search(r'^canon_through:\s*(\d+)', s, re.M)
    if not m:
        errors.append(f'细纲缺 canon_through 状态头: chapters/{n:03d}.md')
    elif int(m.group(1)) != n:
        errors.append(f'细纲 canon_through 错误: chapters/{n:03d}.md 应为 {n}，实际 {m.group(1)}')
    if not re.search(r'^status:\s*\w+', s, re.M):
        errors.append(f'细纲缺 status 状态头: chapters/{n:03d}.md')

# 2c. 卷纲/master/弧纲 canon_through 不得落后于正文（覆盖已写范围的应同步）
for fp in [os.path.join(N, 'outline/master_outline.md'),
           os.path.join(N, 'outline/volume_01.md'),
           os.path.join(N, 'outline/arc_001.md')]:
    m = re.search(r'^canon_through:\s*(\d+)', open(fp, encoding='utf-8').read(), re.M)
    if not m:
        warnings.append(f'缺 canon_through 状态头: {rel(fp)}')
    elif int(m.group(1)) < canon:
        warnings.append(f'{rel(fp)} canon_through({m.group(1)}) < 正文({canon})——覆盖已写章节的应同步')

# ---------- 3. planned 预置 -> 当前事实 扫描 ----------
if os.path.exists(os.path.join(N, 'state/identities.json')):
    ids = json.load(open(os.path.join(N, 'state/identities.json'), encoding='utf-8'))
    for k, v in ids.get('personas', {}).items():
        dc = v.get('planned_debut_chapter') or v.get('debut_chapter')
        if v.get('status') == 'planned' and dc and dc <= canon:
            warnings.append(f'Persona "{k}" 仍 planned 但 debut_chapter({dc}) <= canon({canon})——应转 active')
    for k, v in ids.get('clones', {}).items():
        cc = v.get('created_chapter')
        if v.get('status') == 'planned' and cc and cc <= canon:
            warnings.append(f'Clone "{k}" 仍 planned 但 created_chapter({cc}) <= canon({canon})——应转 active')

if os.path.exists(os.path.join(N, 'state/relationships.json')):
    rl = json.load(open(os.path.join(N, 'state/relationships.json'), encoding='utf-8'))
    for p in rl.get('perceptions', []):
        pc = p.get('planned_establish_chapter')
        if p.get('truth_level') == 'planned' and pc and pc <= canon:
            warnings.append(f'关系 {p["observer"]}->{p["subject"]} 仍 planned 但建立章({pc}) <= canon——检查')

if os.path.exists(os.path.join(N, 'state/foreshadowing.json')):
    fs = json.load(open(os.path.join(N, 'state/foreshadowing.json'), encoding='utf-8'))
    for f in fs.get('foreshadowing', []):
        if f.get('status') == 'planned':
            nums = [int(x) for x in re.findall(r'第\s*(\d+)\s*章', f.get('planned_plant') or '')]
            if nums and min(nums) <= canon:
                warnings.append(f'伏笔 {f["id"]} 仍 planned 但计划埋设章({min(nums)}) <= canon({canon})——应转 planted')

# ---------- 4. 正文禁用词/禁用字符 ----------
# 词表随剧情推进维护：协调局/研究院=ch8 前后才允许；阿尔登=卷二；评级=ch11；异能/超凡=入编后
FORBID_WORDS = ['协调局', '异能', '超凡', '新世界同盟', '研究院']
# [梗] 标注、波浪省略号、中文/英文弯引号（正文一律用 ASCII 直引号 " 或全角中文标点）
BAD_CHARS = ['[梗]', '〜', '～', '﹏', '\u201c', '\u201d', '\u2018', '\u2019']
for n in range(1, canon + 1):
    fp = os.path.join(N, f'chapters/{n:03d}.md')
    if not os.path.exists(fp):
        continue
    t = open(fp, encoding='utf-8').read()
    for w in FORBID_WORDS:
        if w in t:
            errors.append(f'ch{n:03d} 出现禁用词「{w}」（当前章节未到投放阶段）')
    for c in BAD_CHARS:
        if c in t:
            errors.append(f'ch{n:03d} 出现禁用字符 {repr(c)}')

# ---------- 5. decisions 批次/D 编号单调性与唯一性（防乱序/撞号，D-068） ----------
dpath = os.path.join(N, 'meta', 'decisions.md')
dtxt = open(dpath, encoding='utf-8').read()
batches = [int(x) for x in re.findall(r'^# 第(\d+)批', dtxt, re.M)]
if batches != sorted(batches):
    warnings.append(f'decisions 批次编号乱序: {batches}（应递增）')
if len(batches) != len(set(batches)):
    warnings.append(f'decisions 批次编号重复: {len(batches)} 个批次，{len(set(batches))} 个唯一')
dns = [int(x) for x in re.findall(r'^## D-(\d+)', dtxt, re.M)]
if dns != sorted(dns):
    warnings.append(f'decisions D 编号乱序: {dns}')
if len(dns) != len(set(dns)):
    warnings.append(f'decisions D 编号重复: {len(dns)} 个 D，{len(set(dns))} 个唯一（历史撞号事故，禁止再犯）')

# ---------- 6. references 引用存在性 ----------
existing_refs = {os.path.basename(f) for f in glob.glob(os.path.join(N, 'references', '*.md'))}
for src in [dpath, os.path.join(N, 'meta', 'changelog.md')]:
    st = open(src, encoding='utf-8').read()
    for m in re.finditer(r'references/(\d{8}_\d+\.md)', st):
        if m.group(1) not in existing_refs:
            warnings.append(f'{rel(src)} 引用了不存在的 references/{m.group(1)}')

# ---------- 7. git 工作区提示（会话同步确认用，不计数） ----------
try:
    import subprocess
    n_git = len(subprocess.check_output(['git', 'status', '--short'], cwd=ROOT).splitlines())
    print(f'[INFO ] git 工作区 {n_git} 个文件未提交' + ('（定稿后应提交）' if n_git else ''))
    # hook 安装状态（D-070 强制层防丢失）
    hook_src = os.path.join(ROOT, 'scripts', 'hooks', 'pre-commit')
    hook_dst = os.path.join(ROOT, '.git', 'hooks', 'pre-commit')
    if os.path.exists(hook_src):
        if not os.path.exists(hook_dst):
            print('[INFO ] pre-commit hook 未安装，运行 bash scripts/install-hooks.sh（D-070 强制层）')
        elif open(hook_src, encoding='utf-8').read() != open(hook_dst, encoding='utf-8').read():
            print('[INFO ] pre-commit hook 与 scripts/hooks/ 不一致，重新运行 bash scripts/install-hooks.sh')
except Exception:
    pass

# ---------- 8. revision 档案校验（D-069：JSONL 可解析 + 引用文件存在 + 无孤儿） ----------
ridx = os.path.join(N, 'meta', 'revision_index.jsonl')
if os.path.exists(ridx):
    seen = set()
    for ln, line in enumerate(open(ridx, encoding='utf-8'), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception as e:
            errors.append(f'revision_index.jsonl 第 {ln} 行 JSON 损坏: {e}')
            continue
        rfile = os.path.join(N, 'meta', rec.get('file', ''))
        if not os.path.exists(rfile):
            errors.append(f'revision 索引引用文件不存在: {rec.get("file")}')
        seen.add(rec.get('id'))
    rdir = os.path.join(N, 'meta', 'revisions')
    if os.path.isdir(rdir):
        for f in sorted(glob.glob(os.path.join(rdir, 'r*.md'))):
            base = os.path.basename(f)[:-3]
            if base not in seen:
                warnings.append(f'revisions/{base}.md 未在 revision_index.jsonl 登记（孤儿档案）')
else:
    warnings.append('revision_index.jsonl 不存在（首次使用前运行一次）')

# ---------- 输出 ----------
print('=== 一致性审计（canon_through_chapter=%d，%d 个 JSON）===' % (canon, len(json_files)))
print('[SYNC ] 会话同步（D-068）：读取/创作前确认 ①本审计 0 ERROR ②git status 无异常并行改动 ③current.json canon 匹配本次目标章节')
for w in warnings:
    print(f'[WARN ] {w}')
if errors:
    for e in errors:
        print(f'[ERROR] {e}')
    print(f'失败：{len(errors)} 个错误，{len(warnings)} 个警告')
    sys.exit(1)
print(f'通过：0 错误，{len(warnings)} 个警告（警告项定稿时人工确认）')
