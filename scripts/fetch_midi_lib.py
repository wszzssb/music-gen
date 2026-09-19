#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""fetch_midi_lib.py —— 从公开站点抓 MIDI，建**模板库**（音符层参考素材）。

为什么要它：`refs/midi/`（1 号库）是从零散下载攒出来的，来源单一、风格集中。
这个工具**按风格抓**、带**来源可溯**的索引，建出来的库能直接回答
"我要一首爵士/巴洛克/8-bit 的音符层参考，库里有哪首、什么速度、几小节"。

与 `refs/*.json`（音频画像）的分工在 `midi_ref.py` 开头写过：
**音符层看 MIDI（和声/低音/旋律/节奏/曲式，精确），频谱层看音频画像**。两者互补。

来源（全部公开、无需认证）：
  · BitMidi     https://bitmidi.com  —— 搜索 API 给 JSON（多风格：流行/摇滚/爵士/古典/电影/动漫…）
  · VGMusic     https://www.vgmusic.com —— 控制台厂商/平台目录页（游戏音乐，跨年代）
  · Mutopia     https://www.mutopiaproject.org —— 公有领域古典（按作曲家/乐器查询页给直链）

用法:
  python scripts\fetch_midi_lib.py <目标目录> [--per-style N] [--styles a,b,c] [--dry-run]
  # 例：建 2 号模板库（每风格 12 首，共约 200 首）
  python scripts\fetch_midi_lib.py refs\midi2 --per-style 12

产物：`<目标目录>/<风格>/*.mid` + `<目标目录>/_index.json`（bpm/拍号/小节/轨，带风格与来源）。
**断点续传**：已存在的文件跳过，重复跑只补缺的。
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

UA = {'User-Agent': 'Mozilla/5.0 (music-gen template builder; contact: local)'}
DELAY = 0.35            # 每个请求之间的间隔（对站点礼貌；别调太激进）
MAX_BYTES = 900 * 1024  # 单个 MIDI 上限（超大文件多是整部交响曲，对本用途无益）

# 风格 → BitMidi 搜索词（每个风格多给几个词，保证"同风格不同来源"）
STYLES = {
    'classical': ['bach', 'chopin', 'mozart', 'beethoven', 'debussy', 'satie'],
    'baroque':   ['vivaldi', 'handel', 'telemann', 'purcell'],
    'romantic':  ['liszt', 'brahms', 'tchaikovsky', 'grieg'],
    'jazz':      ['jazz', 'swing', 'bebop', 'ragtime', 'dixieland'],
    'blues':     ['blues', 'boogie woogie'],
    'rock':      ['rock', 'hard rock', 'punk', 'grunge'],
    'pop':       ['pop', '80s pop', 'disco', 'boy band'],
    'ballad':    ['ballad', 'love song', 'slow'],
    'electronic': ['techno', 'trance', 'house', 'synthwave', 'chiptune'],
    'folk':      ['folk', 'celtic', 'country', 'bluegrass'],
    'latin':     ['bossa nova', 'samba', 'tango', 'salsa'],
    'film':      ['soundtrack', 'movie theme', 'orchestral'],
    'anime':     ['anime', 'jpop', 'game music'],
    'newage':    ['new age', 'ambient', 'piano solo'],
}

VGMUSIC_PAGES = {          # 游戏音乐按主机代际分（8-bit / 16-bit / 32-bit）
    'chiptune': ['console/nintendo/nes/', 'console/nintendo/gameboy/',
                 'console/sega/mastersystem/', 'console/atari/2600/'],
    'game16':   ['console/nintendo/snes/', 'console/sega/genesis/'],
    'game32':   ['console/sony/ps1/', 'console/nintendo/n64/'],
}


def _get(url, binary=False, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if binary else r.read().decode('utf-8', 'replace')


def _safe(name):
    """清成 Windows 合法文件名。

    ⚠ 顺序很重要：**先补 .mid 后缀，再截断**。反过来的话，名字一超 80 字符，
    .mid 就被截掉了、末尾再补一次 → 变成 ...blogspot.com.mid 里的 .mid 丢失后
    补成 ...blogspot.c.mid/...com.mid，既让 .gitignore 的 *.mid 规则漏网
    （实测有 2 个文件混进了 git），也让 midi_probe 扫不到。"""
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'unnamed'
    if not n.lower().endswith(('.mid', '.midi')):
        n += '.mid'
    base, ext = os.path.splitext(n)
    return base[:80] + ext


def bitmidi(q, limit):
    """BitMidi 搜索 → [(文件名, 绝对下载 URL)]"""
    u = 'https://bitmidi.com/api/midi/search?' + urllib.parse.urlencode({'q': q})
    try:
        j = json.loads(_get(u))
    except Exception as e:
        print('    bitmidi 搜索失败 %-12s %s' % (q, e))
        return []
    out = []
    for it in (j.get('result', {}).get('results') or [])[:limit]:
        du = it.get('downloadUrl')
        if du:
            out.append((it.get('name') or ('m%d' % it.get('id', 0)),
                        urllib.parse.urljoin('https://bitmidi.com', du)))
    return out


def vgmusic(page, limit):
    """VGMusic 平台页 → [(文件名, 绝对下载 URL)]（页面里的 .mid 是相对链接）"""
    base = 'https://www.vgmusic.com/music/' + page
    try:
        html = _get(base)
    except Exception as e:
        print('    vgmusic 打不开 %-28s %s' % (page, e))
        return []
    hrefs = re.findall(r'href="([^"]+\.mid)"', html, re.I)
    return [(_safe(os.path.basename(h)), urllib.parse.urljoin(base, h))
            for h in hrefs[:limit]]


def mutopia(limit):
    """Mutopia 查询页 → [(文件名, 绝对 URL)]（公有领域古典）"""
    out = []
    for comp in ('BachJS', 'ChopinFF', 'MozartWA', 'BeethovenLv', 'DebussyC',
                 'SatieE', 'HandelGF', 'VivaldiA'):
        try:
            u = ('https://www.mutopiaproject.org/cgibin/make-table.cgi?Composer=%s'
                 '&instrument=piano' % comp)
            html = _get(u)
        except Exception:
            continue
        for h in re.findall(r'href="([^"]+\.mid)"', html, re.I)[:max(1, limit // 4)]:
            out.append((_safe(os.path.basename(h)), urllib.parse.urljoin(u, h)))
        time.sleep(DELAY)
    return out


def fetch(url, dest):
    """下载并做**轻量校验**（MThd 头 + 大小）→ True/False"""
    try:
        data = _get(url, binary=True)
    except Exception as e:
        print('      下载失败 %s' % str(e)[:70])
        return False
    if len(data) < 64 or not data.startswith(b'MThd'):
        print('      不是合法 MIDI（跳过）')
        return False
    if len(data) > MAX_BYTES:
        print('      太大 %.0fKB（跳过）' % (len(data) / 1024))
        return False
    with open(dest, 'wb') as f:
        f.write(data)
    return hashlib.md5(data).hexdigest()


def _division(path):
    """MIDI 头部第 13-14 字节 = 每四分音符的 tick 数（自己读，免得改 `midi_probe` 的返回）"""
    d = open(path, 'rb').read(14)
    return int.from_bytes(d[12:14], 'big') or 480 if d[:4] == b'MThd' else 480


def build_index(root):
    """用 `midi_probe.parse` 的口径建特征索引（与 1 号库同格式：每轨只存音符数/音域，
    不存音符数组）+ 风格标签 + md5（跨来源去重）。

    **解析失败的文件移到 `_broken/`**：实测抓到过"头部声明 9 轨、后面没有 MTrk"的
    残缺下载（BitMidi 上偶见）。留在库里会让索引与磁盘对不上（自检 `midi_lib_index_sync`
    会 FAIL），所以隔离而不删除 —— 保留证据，但不参与选曲。`_` 开头的目录一律跳过。
    """
    import midi_probe
    rows, seen = [], {}
    broken = os.path.join(root, '_broken')
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('_')]
        for fn in sorted(files):
            if not fn.lower().endswith(('.mid', '.midi')):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace('\\', '/')
            style = rel.split('/')[0] if '/' in rel else 'root'
            data = open(full, 'rb').read()
            h = hashlib.md5(data).hexdigest()
            if h in seen:                       # 内容重复（同曲不同名）只留一个
                continue
            seen[h] = rel
            try:
                info = midi_probe.parse(full, quiet=True)
            except Exception as e:
                os.makedirs(broken, exist_ok=True)
                os.replace(full, os.path.join(broken, fn))
                print('    解析失败 → 移入 _broken/：%-38s %s' % (rel, str(e)[:40]))
                continue
            tr = []
            for t in info.get('tracks', []):
                ns = t.get('notes') or []
                if not ns:
                    continue
                ps = [n[2] for n in ns]
                tr.append({'index': t.get('index'), 'name': t.get('name'),
                           'channel': t.get('channel'), 'program': t.get('program'),
                           'notes': len(ns), 'lo': min(ps), 'hi': max(ps)})
            bpm = info.get('bpm') or 120.0
            ts = info.get('timesig') or (4, 4)
            num, den = ts
            bb = num * 4.0 / den                       # 一小节的四分音符数
            end_q = (info.get('end_tick') or 0) / float(_division(full))
            rows.append({
                'file': rel, 'style': style, 'md5': h, 'bytes': len(data),
                'bpm': round(bpm, 2), 'timesig': list(ts),
                'bars': round(end_q / bb, 2) if bb else 0.0,
                'seconds': round(end_q * 60.0 / bpm, 1),
                'note_count': info.get('note_count', 0), 'tracks': tr,
            })
    rows.sort(key=lambda r: (r['style'], r['file']))
    with open(os.path.join(root, '_index.json'), 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return rows


def repair(root):
    """按 `_sources.json` 的原始 URL 逐首重下**缺失**的 MIDI（2026-09-19 事故恢复用）。

    为什么单独一个入口：`main()` 是**按搜索词**抓的（每次结果不同，抓回来的是另一批文件），
    而 `_sources.json` 记了逐首的**原始下载 URL** —— 拿它逐首重下才能拿回**同一批**文件
    （实测抽样 6/6：内容 md5 与 `_index.json` 逐首一致）。

    为什么需要它：`refs/midi2/*.mid` 被 .gitignore 排除（版权 + 体积），
    本地误删后 **git 恢复不了**（只能恢复 `_index.json` / `_sources.json`）；
    而主题包校验、写歌本身都**不读** MIDI 文件（读索引与画像），所以库缺失不会立刻暴露。
    """
    idx_p = os.path.join(root, '_index.json')
    src_p = os.path.join(root, '_sources.json')
    if not (os.path.isfile(idx_p) and os.path.isfile(src_p)):
        print('缺 _index.json / _sources.json（这两个是入库的，先 git checkout 恢复）')
        return 1
    idx = {r['file']: r for r in json.load(open(idx_p, encoding='utf-8'))}
    src = json.load(open(src_p, encoding='utf-8'))
    todo = []
    for f, url in src.items():
        if f not in idx:
            continue
        dest = os.path.join(root, *f.split('/'))
        if not os.path.isfile(dest):
            todo.append((f, url, dest, idx[f].get('md5')))
    print('索引 %d 首 · 来源 %d 条 · **缺 %d 首**' % (len(idx), len(src), len(todo)))
    if not todo:
        print('库是完整的（没有缺文件）')
        return 0
    got = fail = 0
    badmd5 = []
    for i, (f, url, dest, want) in enumerate(todo, 1):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except Exception as e:                                     # noqa: BLE001
            print('  [%d/%d] 失败 %s → %s' % (i, len(todo), f, str(e)[:60]))
            fail += 1
            time.sleep(DELAY)
            continue
        h = hashlib.md5(data).hexdigest()
        with open(dest, 'wb') as fh:
            fh.write(data)
        if want and h != want:
            badmd5.append(f)
            print('  [%d/%d] md5 不符 %s（服务器内容变了）' % (i, len(todo), f))
        else:
            got += 1
        if i % 20 == 0 or i == len(todo):
            print('  … 进度 %d/%d（成功 %d · 失败 %d）' % (i, len(todo), got, fail))
        time.sleep(DELAY)
    print('\n恢复 %d 首 · 失败 %d · md5 不符 %d' % (got, fail, len(badmd5)))
    if badmd5:
        print('  md5 不符（索引该重算）：%s' % ', '.join(badmd5[:8]))
    if fail:
        print('  失败的多半是 URL 失效 —— 这些首需要重抓或从索引里剔除')
    return 0 if not fail else 1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    root = args[0]
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), root)
    per = int(sys.argv[sys.argv.index('--per-style') + 1]) \
        if '--per-style' in sys.argv else 8
    only = sys.argv[sys.argv.index('--styles') + 1].split(',') \
        if '--styles' in sys.argv else None
    dry = '--dry-run' in sys.argv
    if '--repair' in sys.argv:
        return repair(root)
    os.makedirs(root, exist_ok=True)
    ver = os.path.join(root, '_sources.json')
    src = json.load(open(ver, encoding='utf-8')) if os.path.isfile(ver) else {}

    total_new = total_skip = 0
    for style, words in STYLES.items():
        if only and style not in only:
            continue
        d = os.path.join(root, style)
        os.makedirs(d, exist_ok=True)
        print('== %s（目标 %d 首）' % (style, per))
        cand = []
        for w in words:
            cand += bitmidi(w, max(3, per // len(words) + 2))
            time.sleep(DELAY)
        for name, url in cand:
            fn = _safe(name)
            dest = os.path.join(d, fn)
            if os.path.isfile(dest):
                total_skip += 1
                continue
            if dry:
                print('    [dry] %s' % fn)
                continue
            got = fetch(url, dest)
            if got:
                src['%s/%s' % (style, fn)] = url
                total_new += 1
                print('    + %s' % fn)
            time.sleep(DELAY)
            if sum(1 for x in os.listdir(d)) >= per:
                break

    for style, pages in VGMUSIC_PAGES.items():
        if only and style not in only:
            continue
        d = os.path.join(root, style)
        os.makedirs(d, exist_ok=True)
        print('== %s（目标 %d 首）' % (style, per))
        for pg in pages:
            for name, url in vgmusic(pg, per):
                fn = _safe(name)
                dest = os.path.join(d, fn)
                if os.path.isfile(dest):
                    total_skip += 1
                    continue
                if dry:
                    print('    [dry] %s' % fn)
                    continue
                if fetch(url, dest):
                    src['%s/%s' % (style, fn)] = url
                    total_new += 1
                    print('    + %s' % fn)
                time.sleep(DELAY)
                if sum(1 for x in os.listdir(d)) >= per:
                    break
            if sum(1 for x in os.listdir(d)) >= per:
                break

    if not dry and (only is None or 'classical' in (only or [])):
        d = os.path.join(root, 'public_domain')
        os.makedirs(d, exist_ok=True)
        print('== public_domain（Mutopia 公有领域古典）')
        for name, url in mutopia(per):
            dest = os.path.join(d, _safe(name))
            if os.path.isfile(dest):
                total_skip += 1
                continue
            if fetch(url, dest):
                src['public_domain/%s' % _safe(name)] = url
                total_new += 1
                print('    + %s' % name)
            time.sleep(DELAY)

    json.dump(src, open(ver, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n下载 %d 首、跳过 %d 首；来源表 → %s' % (total_new, total_skip, ver))
    if not dry:
        rows = build_index(root)
        from collections import Counter
        c = Counter(r['style'] for r in rows)
        print('索引 %d 首 → %s' % (len(rows), os.path.join(root, '_index.json')))
        for k in sorted(c):
            print('  %-14s %3d 首' % (k, c[k]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
