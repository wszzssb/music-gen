# -*- coding: utf-8 -*-
r"""name_rules.py —— **曲目名合法性**（面板与脚本共用的**唯一口径** · 2026-10-09 放开中文与空格）

为什么要有它：曲目名（`id`）**同时是目录名、文件名前缀、命令行参数与 URL 参数**。
2026-10-09 之前是三处各写一条 `^[0-9A-Za-z_][0-9A-Za-z_-]{0,40}$` 的白名单 ——
用户问"为什么不能有中文和空格"，查下来：

  · **必须挡的**：`id` 就是目录名（`songs_list` 取目录名 · `song_dir(sid)=join(曲库,sid)`）
    ⇒ `/` `\` `..` 这些必须挡（`studio/server.py` 里对编辑器会话 id 写明的理由就是"防路径穿越"）；
  · **工程性的**：同一个串还当 `<id>.mid` / `<id>_sf.ogg` / `<id>_solo` 文件名、
    `new_song.py <id>` 这类 **argv**（⇒ **不能以 `-` 开头**，否则被 argparse 当选项）、
    `/api/...?id=<id>` 这类 **URL 参数**、以及 JOB id 与日志。
  · **不是"中文做不到"**：仓库全程处理中文/日文**路径**（`…\ピュアソングガーデン！解包\Bgm\BGM23.ogg`
    跑通了整条链）⇒ 中文与空格本身是安全的，禁的是**路径元字符**。

用法：
    from name_rules import check_song_name, SONG_NAME_HINT
    name, err = check_song_name(raw)     # err 为空 = 合法；`name` 是**规范化后**的名字（NFC + 去首尾空白）
    if err: 报错(err)

⚠ **JS 侧是镜像**（`studio/web/create.js` 的 `nameErr()`）：两份**必须同源**，
   守卫 `selftest.t_song_name_rules` 会比对（要求 `SONG_NAME_HINT`、`MAX_LEN`、
   禁用字符表、保留名表在 JS 里逐项对得上）—— 改这里就得改那边。
"""
import unicodedata

MAX_LEN = 48

#: 路径分隔符 + Windows 非法字符（**这是"必须挡"的那一半**）
FORBIDDEN = '/\\:*?"<>|'

#: Windows 保留设备名（大小写无关；带后缀也算，如 `CON.txt`）
RESERVED = frozenset(
    ['CON', 'PRN', 'AUX', 'NUL']
    + ['COM%d' % i for i in range(1, 10)]
    + ['LPT%d' % i for i in range(1, 10)]
)

#: 给用户看的规则说明（**JS 里必须逐字相同** —— 守卫比对这条串）
SONG_NAME_HINT = ('曲目名可以用中文/字母/数字/空格 · - _ . ——'
                  '不能含 / \\ : * ? " < > |，不能以 - 或 . 开头，最多 %d 字' % MAX_LEN)


def check_song_name(raw):
    """→ `(name, err)`。`err` 非空 = 不合法（直接给用户看这句话）。

    顺序有意：先规范化 → 长度 → 字符 → 路径/Windows 细节。
    """
    name = unicodedata.normalize('NFC', str(raw or '')).strip()
    if not name:
        return '', '曲目名不能为空'
    if len(name) > MAX_LEN:
        return '', '曲目名太长：最多 %d 个字符（当前 %d）' % (MAX_LEN, len(name))
    bad = sorted({c for c in name if c in FORBIDDEN or ord(c) < 32})
    if bad:
        return '', ('曲目名不能含这些字符：%s —— 它们是路径/文件名元字符'
                    '（曲目名同时是目录名）' % ' '.join(bad))
    if name in ('.', '..') or '..' in name:
        return '', '曲目名不能含 ".."（防路径穿越）'
    if name[0] in '.-':
        return '', '曲目名不能以 "-" 或 "." 开头（前者会被命令行当选项，后者会被当隐藏项）'
    if name[-1] in '. ':
        return '', '曲目名结尾不能是点或空格（Windows 会把它们吃掉）'
    stem = name.split('.')[0].upper()
    if stem in RESERVED:
        return '', '曲目名不能是 Windows 保留名（%s）' % stem
    return name, ''


def ok(raw):
    """只问"合不合法"（自检/单测用）。"""
    return check_song_name(raw)[1] == ''


def tidy_for_filename(raw, fallback='song'):
    """**给"必须是文件名"的场合**用（如中转目录名）：不合法就退回一个安全串。

    ⚠ 与 `check_song_name` 不同：这里是**兜底**、不是校验 —— 只给确实需要
    "一定能落盘"的地方用（例如上传音频的中转目录）；曲目 id 一律走 `check_song_name`。
    """
    name, err = check_song_name(raw)
    if err:
        return fallback
    return name
