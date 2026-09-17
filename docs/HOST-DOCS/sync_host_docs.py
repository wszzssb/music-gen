# -*- coding: utf-8 -*-
"""把宿主级文档备份进仓库（它们本来不在 git 里，重装/换机就丢）。

备份对象：`~/.dsh/AGENTS.md` 与 `~/.dsh/docs/*.md`。
**真正生效的是 `~/.dsh/` 下那份** —— 仓库这份只是版本历史；每个文件顶部都加了醒目说明，
防止以后有人改了仓库这份却发现"没生效"。
"""
import os
import shutil

HOME = os.path.expanduser('~')
DST = r'D:\software\skill\docs\HOST-DOCS'
SRC = [
    (os.path.join(HOME, '.dsh', 'AGENTS.md'), 'AGENTS.md'),
    (os.path.join(HOME, '.dsh', 'docs', 'COT-PERSONA.md'), 'COT-PERSONA.md'),
    (os.path.join(HOME, '.dsh', 'docs', 'SHELL-NOTES.md'), 'SHELL-NOTES.md'),
]
BANNER = ('<!-- ⚠ 这是**备份**，不是生效副本：真正被 DSH 加载的是宿主上的\n'
          '     `%s`。要改请改那份（改完新会话生效），再重跑 `sync_host_docs.py` 同步过来。 -->\n\n')

os.makedirs(DST, exist_ok=True)
for src, name in SRC:
    if not os.path.isfile(src):
        print('  跳过（源不存在）：%s' % src)
        continue
    txt = open(src, encoding='utf-8').read()
    with open(os.path.join(DST, name), 'w', encoding='utf-8') as f:
        f.write(BANNER % src.replace('\\', '/') + txt)
    print('  %-16s → docs/HOST-DOCS/%-16s (%d 字节)' % (name, name, len(txt)))

# 同步脚本也留一份，免得下次又靠手抄
shutil.copyfile(os.path.abspath(__file__), os.path.join(DST, 'sync_host_docs.py'))
print('  sync_host_docs.py → docs/HOST-DOCS/（下次直接跑它）')
