# -*- coding: utf-8 -*-
"""抓 Mutopia Project 的钢琴曲（公有领域古典，许可 PD/CC —— 可再分发）。

用户口径："可以抓的都抓，转录许可不明不用，按照风格分类"。
Mutopia 的授权在**每首曲子的页面上**（PD / CC BY / CC BY-SA），本脚本：
  · 抓 `make-table.cgi?Composer=<X>&instrument=piano` 页面里的 .mid 直链
  · 顺便把该页里的**曲目名/作曲家**解析出来（页面是表格，行里有标题与文件链接）
  · 按**作曲家**分目录（Mutopia 页面本身按作曲家组织；风格标签它不提供，
    所以风格记成 `classical_pd`，并在索引里带上作曲家 + 年代线索）
"""
import collections
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r'D:\software\skill\music-gen\scripts')
import cli_utf8 as _cu

_cu.setup()

OUT = r'D:\software\skill\music-gen\refs\classical_pd\mutopia'
UA = {'User-Agent': 'Mozilla/5.0 (music-gen template builder; local)'}
COMPOSERS = ['BachJS', 'ChopinFF', 'MozartWA', 'BeethovenLv', 'DebussyC', 'SatieE',
             'HandelGF', 'VivaldiA', 'LisztF', 'BrahmsJ', 'SchubertF', 'GriegE',
             'ScarlattiD', 'PurcellH', 'TelemannGP', 'MendelssohnF', 'SchumannR',
             'HaydnFJ', 'RavelM', 'FaureG']
os.makedirs(OUT, exist_ok=True)


def get(url, binary=False, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if binary else r.read().decode('utf-8', 'replace')


def safe(s, n=80):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(s or '')).strip(' .')
    return (s[:n] or 'unnamed')


rows = []
for comp in COMPOSERS:
    u = ('https://www.mutopiaproject.org/cgibin/make-table.cgi?Composer=%s'
         '&instrument=piano' % comp)
    try:
        html = get(u)
    except Exception as e:                                        # noqa: BLE001
        print('  %-14s 打不开：%s' % (comp, str(e)[:60]))
        continue
    # 页面里每个 .mid 链接前后有曲名；粗暴但有效：按 <tr> 切行，行内找 .mid 与文本
    got = 0
    for m in re.finditer(r'href="([^"]+\.mid)"', html, re.I):
        href = m.group(1)
        url = urllib.parse.urljoin(u, href)
        fn = os.path.basename(href)
        # 行内文本当曲名（取该链接所在 <tr>）
        seg = html.rfind('<tr', 0, m.start())
        title = ''
        if seg >= 0:
            tr = html[seg:html.find('</tr>', m.start())]
            txt = re.sub(r'<[^>]+>', ' ', tr)
            txt = re.sub(r'\s+', ' ', txt).strip()
            title = safe(txt[:60], 60)
        d = os.path.join(OUT, comp)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, safe(fn))
        if os.path.exists(dst):
            got += 1
            continue
        try:
            blob = get(url, binary=True)
        except Exception:                                        # noqa: BLE001
            continue
        if not blob or blob[:4] != b'MThd':
            continue
        with open(dst, 'wb') as f:
            f.write(blob)
        rows.append({'file': os.path.relpath(dst, OUT).replace(os.sep, '/'),
                     'composer': comp, 'title': title, 'source': url,
                     'license': 'Mutopia (PD / CC — 逐首见来源页)',
                     'md5': hashlib.md5(blob).hexdigest(), 'bytes': len(blob)})
        got += 1
        time.sleep(0.25)
    print('  %-14s %3d 首' % (comp, got))

p = os.path.join(OUT, '_index.json')
old = json.load(io.open(p, encoding='utf-8')) if os.path.exists(p) else []
have = {r['file'] for r in old}
rows = old + [r for r in rows if r['file'] not in have]
json.dump(rows, io.open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nMutopia 入库 %d 首 → %s' % (len(rows), OUT))
for k, v in collections.Counter(r['composer'] for r in rows).most_common():
    print('  %-14s %d' % (k, v))
