#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""曲库体检：找出**悬空 junction** 与**纯音频目录**（面板「读不到曲目」的真凶）。

为什么要有它（2026-09-21 实测）：
  面板报「曲目 xxx 是**纯音频目录**（里面只有音频、没有 song.json）」时，用户会以为曲子坏了。
  真凶有两类，**都不在面板**：
    ① **悬空 junction** —— 曲库条目是指向 `songs/<曲目>` 的 junction，源目录被删（或曲子改名）
       之后链接留在曲库里。Windows 下 `Test-Path` 对它**返回 True**、`os.path.isdir` 也是 True，
       只有真的去读内容才报 "Could not find a part of the path" —— 于是面板"列得出来、读不进去"。
       （判据坑详见 `~/.dsh/docs/FS-LINK-NOTES.md`）
    ② **纯音频目录** —— 有意为之的 A/B 试听目录（面板 `need_json=False` 时放行，见 server.py:241）。
       面板支持它，但**交付物/中间产物不该放进来**：曲库是"能读的曲目"的目录。

用法:
  python scripts\\studio_audit.py                 # 体检（默认曲库 = projects.json 里的）
  python scripts\\studio_audit.py --lib <目录>     # 指定曲库
  python scripts\\studio_audit.py --clean         # 删掉**悬空 junction**（只删链接，不碰真身）
  python scripts\\studio_audit.py --json          # 机器可读
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu          # noqa: E402

_cu.setup()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def default_lib():
    """默认曲库根 —— 与面板**同一套优先级**（`studio/server.py` main 里那段）。

    ⚠ 为什么不能写死（2026-09-30 实测）：原来硬编码 `D:\\test\\dt_midi`，
    而那个目录**早已不存在**；同时**面板真正用的**是 `studio/.libpath` 里记的
    `D:\\test\\siren_end2_交付`（一个只有 1 首、且没有 song.json 的交付目录，
    面板于是走 `probe_lib` 的 single 布局）。结果是：**面板显示 1 首、
    这个体检工具却在报另一个不存在的目录「0 项」** —— 两个工具说的不是同一件事，
    "曲库坏了" 的排查会从一开始就走错方向。
    优先级：`--lib`（调用方显式给）> `BGM_STUDIO_LIB` > `studio/.libpath` > 工具链 `songs/`。
    """
    lp = os.path.join(ROOT, 'studio', '.libpath')
    for cand in (os.environ.get('BGM_STUDIO_LIB'),
                 (open(lp, encoding='utf-8').read().strip()
                  if os.path.isfile(lp) else None)):
        if cand and os.path.isdir(cand):
            return cand
    return os.path.join(ROOT, 'songs')


DEFAULT_LIB = default_lib()


def _is_link_like(path):
    """是不是 junction / symlink。

    ⚠ **`os.path.islink` 对 Windows 目录 junction 返回 False**（实测；它只认 real symlink）
    —— 我第一版就是靠它判的，结果 8 个悬空 junction 全被当成"散装文件"，`--clean` 一个都删不掉。
    改用 `os.path.isjunction`（Python 3.12+）+ `os.readlink` + `dir /AL` 三重兜底。
    （判据坑见 `~/.dsh/docs/FS-LINK-NOTES.md`）
    """
    if hasattr(os.path, 'isjunction') and os.path.isjunction(path):
        return True
    try:
        os.readlink(path)
        return True
    except OSError:
        pass
    try:
        out = subprocess.run(['cmd', '/c', 'dir', '/AL', os.path.dirname(path)],
                             capture_output=True, text=True, errors='replace').stdout
        return any(os.path.basename(path) in ln and '<JUNCTION>' in ln.upper()
                   for ln in out.splitlines())
    except Exception:                                            # noqa: BLE001
        return False


def link_target(path):
    """取 junction/symlink 的目标；不是链接返回 None。"""
    try:
        return os.readlink(path)
    except OSError:
        pass
    try:
        out = subprocess.run(['cmd', '/c', 'dir', '/AL', os.path.dirname(path)],
                             capture_output=True, text=True, errors='replace').stdout
        for ln in out.splitlines():
            if os.path.basename(path) in ln and '[' in ln and ']' in ln:
                return ln[ln.index('[') + 1:ln.rindex(']')]
    except Exception:                                            # noqa: BLE001
        pass
    return None


def scan(lib):
    rows = []
    if not os.path.isdir(lib):
        return rows
    for name in sorted(os.listdir(lib)):
        p = os.path.join(lib, name)
        # ⚠ **判断顺序很重要**（第一版连着错两次，都是"顺序/判据"问题）：
        #   第一次：没分文件/目录 → 散装 `.mid` 被标成"纯音频目录"；
        #   第二次：先 `os.path.isdir(p)` → **悬空 junction 的 isdir 返回 False**，
        #          于是 8 个悬空链接被标成"散装文件"，`--clean` 一个都删不掉。
        # 正解：`lexists`（链接悬空也为真）→ `islink` → 才是目录/文件之分。
        if not os.path.lexists(p):
            rows.append({'name': name, 'path': p, 'target': None, 'kind': '不存在',
                         'has_song_json': False, 'readable': False})
            continue
        is_link = _is_link_like(p)
        tgt = None
        if is_link:
            tgt = link_target(p) or '(读不到目标)'
        readable = True
        try:
            os.listdir(p)
        except OSError:
            readable = False
        # ⚠ 悬空 junction 下 `isdir` 返回 **False**，所以判定顺序必须是：
        #    链接(悬空/正常) → 目录 → 文件，**不能**先问"是不是目录"。
        if is_link and not readable:
            kind = '悬空链接'
        elif is_link:
            kind = '链接'
        elif os.path.isdir(p) and not os.listdir(p):
            # ⚠ 空目录**不是**「纯音频目录」（2026-09-30 实测：`songs_direct\\export\\` 空目录
            #   被标成"纯音频目录"，用户会以为那里有东西坏了）。它既没有 song.json、
            #   也没有音频 → 面板的 `probe_lib`/`has_audio` 根本不会把它当曲目。
            #   单独一类，处置也不同：直接删（它是 `EXPORT_DIR` 的残留）。
            kind = '空目录'
        elif os.path.isdir(p) and not os.path.isfile(os.path.join(p, 'song.json')):
            kind = '纯音频目录'
        elif os.path.isdir(p):
            kind = '正常曲目'
        else:
            kind = '散装文件'
        rows.append({'name': name, 'path': p, 'target': tgt, 'kind': kind,
                     'has_song_json': os.path.isfile(os.path.join(p, 'song.json')),
                     'readable': readable})
    return rows


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--lib', default=DEFAULT_LIB,
                    help='曲库根；默认与面板同一套优先级（%s）' % DEFAULT_LIB)
    ap.add_argument('--clean', action='store_true', help='删掉悬空 junction（只删链接）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    rows = scan(a.lib)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print('曲库：%s（%d 项）' % (a.lib, len(rows)))
        bad = [r for r in rows if r['kind'] in ('悬空链接', '纯音频目录', '散装文件', '空目录')]
        for r in rows:
            mark = {'正常曲目': 'OK  ', '链接': 'OK  ', '纯音频目录': '音频',
                    '悬空链接': '悬空', '散装文件': '散件', '空目录': '空  '}[r['kind']]
            print('  %s %-36s %s' % (mark, r['name'], r['kind']))
        print('\n合计：正常 %d · 纯音频目录 %d · 悬空链接 %d · 散装文件 %d · 空目录 %d'
              % (sum(1 for r in rows if r['kind'] in ('正常曲目', '链接')),
                 sum(1 for r in rows if r['kind'] == '纯音频目录'),
                 sum(1 for r in rows if r['kind'] == '悬空链接'),
                 sum(1 for r in rows if r['kind'] == '散装文件'),
                 sum(1 for r in rows if r['kind'] == '空目录')))
        if bad:
            print('⚠ 面板能列出它们，但读 `song.json` 的操作会报「纯音频目录 / 读不到曲目」——'
                  '这是**曲库里的链接残留与散装文件**，不是曲子坏了。')
            print('  · 悬空链接 → `--clean` 清理（只删链接，不碰源目录）')
            print('  · 纯音频目录 → 有意做 A/B 试听可留（面板 `need_json=False` 放行）；'
                  '**交付物/中间产物请移出曲库**')
            print('  · 散装文件 → 曲库只放**曲目目录**；音频文件请放进曲目目录或曲库外的交付目录')
            print('  · 空目录 → 通常是 `export/` 残留（面板不会当曲目）；确认无用后手动删')
    if a.clean:
        n = 0
        for r in rows:
            if r['kind'] == '悬空链接':
                try:
                    # ⚠ junction 用 `os.rmdir`（只删链接本身，不碰目标）；目标已不存在时
                    # Windows 上有时要退回 `os.remove`（悬空链接可能被当成占位文件）
                    try:
                        os.rmdir(r['path'])
                    except OSError:
                        os.remove(r['path'])
                    n += 1
                    print('  已删悬空链接：%s（原指向 %s）' % (r['name'], r['target']))
                except OSError as e:
                    print('  删不掉 %s：%s' % (r['name'], e))
        print('清理完成：删除 %d 个悬空链接（只删了链接，源目录未动）' % n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
