# -*- coding: utf-8 -*-
"""抓 HuggingFace `TiMauzi/imslp-midi-cc0-1.0` 里**逐行明确 CC0** 的条目。

用户口径（2026-09-21）："可以抓的都抓，转录许可不明不用，按照风格分类"。
  · **只用逐行 `license == cc0-1.0` 的**（tag 是全库声明，不等于每一行 —— 实测 890 行里
    只有 237 行有值，653 行为空，且空的那批正是 Bach/Vivaldi/Mozart 这些知名作曲家，
    来源是 IMSLP 的文件页、转录许可未标注 → **不收**）
  · 按数据集的 `style` 字段分目录（Romantic / Baroque / Modern / Classical…）
  · 逐首写 `_index.json`：文件名 / 来源 URL / 许可 / 作曲家 / 年代 / 调性 / 字节数
"""
import collections
import hashlib
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, r'D:\software\skill\music-gen\scripts')
import cli_utf8 as _cu

_cu.setup()
import pyarrow.parquet as pq

HF = r'D:\test\_tmp\piano-rain\hf_cc0'
OUT = r'D:\software\skill\music-gen\refs\classical_pd'
SHARDS = ('train', 'validation', 'test')


def ensure_shard(name):
    p = os.path.join(HF, '%s.parquet' % name)
    if os.path.exists(p):
        return p
    url = ('https://hf-mirror.com/datasets/TiMauzi/imslp-midi-cc0-1.0/resolve/main/data/'
           '%s-00000-of-00001.parquet' % name)
    print('  下载 %s …' % name)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=180) as r, open(p, 'wb') as f:
        while True:
            c = r.read(1 << 20)
            if not c:
                break
            f.write(c)
    return p


def safe(s, n=70):
    import re
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(s or '')).strip(' .')
    return (s[:n] or 'unnamed')


os.makedirs(OUT, exist_ok=True)
rows = []
for sh in SHARDS:
    p = ensure_shard(sh)
    t = pq.read_table(p)
    cols = {c: t.column(c).to_pylist() for c in t.column_names}
    n0 = len(cols['file_name'])
    for i in range(n0):
        lic = cols['license'][i]
        if (lic or '').strip().lower() != 'cc0-1.0':
            continue                      # ← 用户口径：许可不明的一律不收
        blob = cols['midi'][i]
        if not blob or blob[:4] != b'MThd':
            print('    跳过（不是 MIDI）：%s' % cols['file_name'][i])
            continue
        style = safe(cols['style'][i] or 'Other', 30)
        comp = safe(cols['composer'][i] or 'Unknown', 40)
        base = '%s - %s' % (comp, safe(cols['title'][i] or cols['file_name'][i], 50))
        fn = base[:80] + '.mid'
        d = os.path.join(OUT, 'hf_cc0', style)
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, fn)
        k = 1
        while os.path.exists(fp):
            fp = os.path.join(d, '%s_%d.mid' % (base[:76], k))
            k += 1
        with open(fp, 'wb') as f:
            f.write(blob)
        rows.append({
            'file': os.path.relpath(fp, OUT).replace(os.sep, '/'),
            'style': style,
            'composer': cols['composer'][i],
            'title': cols['title'][i],
            'era': cols['era'][i],
            'key': cols['key'][i],
            'year': cols['year'][i],
            'license': 'cc0-1.0',
            'source': cols['midi_source'][i] or cols['metadata_source'][i],
            'shard': sh,
            'md5': hashlib.md5(blob).hexdigest(),
            'bytes': len(blob),
        })
    print('  %-11s 读 %d 行' % (sh, n0))

idx_p = os.path.join(OUT, '_index_hf_cc0.json')
json.dump(rows, io.open(idx_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nCC0 入库 %d 首 → %s' % (len(rows), OUT))
print('风格分布：')
for k, v in collections.Counter(r['style'] for r in rows).most_common():
    print('  %-22s %d' % (k, v))
