#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""asset_versions.py —— 前端资源的**版本清单**：改了 `studio/web/*.js|*.css` 就必须升 `?v=`

用法:
  python scripts\asset_versions.py --write    # 改完 js/css **并升了** html 里的 `?v=` 之后跑：刷新清单
  python scripts\asset_versions.py --check    # 只检查（自检 `t_frontend_versions` 用同一份口径）
  python scripts\asset_versions.py            # 同 --check

**为什么要有它**（2026-10-08 实测踩过）：浏览器 / WebView2 按 **URL** 缓存，`?v=` 是唯一的破缓存
手段。我改了 `studio/web/create.js`（创作台的「想要的时长」提示）却**忘了升** `create.html` 里的
`?v=12` ⇒ 面板照样给浏览器旧文件，用户"改了却看不到"（仓库口径：这类事故有一半是这个）。
而**自检里一条盯它的检查都没有** ⇒ 这次补上：清单记 `{文件: {v, sha}}`
  · `v`   = `*.html` 里引用的版本号（**同一个文件在多页里必须一致**，不一致直接报）
  · `sha` = 该文件当前内容的 sha256 前 16 位（内容一变就与清单不符 ⇒ 提示"升号 + 重跑 --write"）

口径：
  · 只查 `studio/web/*.html` 里 `src=`/`href=` 引用的**本地** `.js`/`.css`（CDN / data: 不看）
  · 被引用的资源**必须有 `?v=N`**（没有 = 改了必然被缓存，直接报）
  · 资源**必须登记在清单里**（新加的 js/css 漏登记 = 无人盯）
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, 'studio', 'web')
MANIFEST = os.path.join(WEB, '_versions.json')
# `src="/x.js?v=3"` / `href="/x.css?v=3"`；版本号缺失也要能抓到（所以 ?v= 是可选的）
REF_RE = re.compile(r'(?:src|href)="(/[^"?]+\.(?:js|css))(\?v=(\d+))?"')
MIN_REFS = 8          # 扫到的引用少于这个数 = 判据在空转（页面改名/搬走会这样）


def sha16(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def scan_refs():
    """→ {资源名(如 `create.js`): {'v': {版本号: [引用它的 html]}, 'none': [没写版本号的 html]}}"""
    out = {}
    try:
        files = sorted(os.listdir(WEB))
    except OSError:
        return out
    for f in files:
        if not f.endswith('.html'):
            continue
        try:
            txt = open(os.path.join(WEB, f), encoding='utf-8').read()
        except OSError:
            continue
        for m in REF_RE.finditer(txt):
            name = os.path.basename(m.group(1))
            rec = out.setdefault(name, {'v': {}, 'none': []})
            if m.group(3):
                rec['v'].setdefault(int(m.group(3)), []).append(f)
            else:
                rec['none'].append(f)
    return out


def load_manifest():
    try:
        with open(MANIFEST, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def problems():
    """→ (问题清单, 统计信息) —— 守卫与 `--check` 共用这一份口径"""
    refs = scan_refs()
    man = load_manifest()
    bad, n = [], 0
    for name, rec in sorted(refs.items()):
        path = os.path.join(WEB, name)
        n += len(rec['none']) + sum(len(v) for v in rec['v'].values())
        if not os.path.isfile(path):
            bad.append('`%s` 被 html 引用但文件不存在（%s）'
                       % (name, '、'.join(rec['none'] + [h for v in rec['v'].values() for h in v])))
            continue
        if rec['none']:
            bad.append('`%s` 在被引用时**没写 `?v=`**（%s）—— 改了必然被浏览器缓存，'
                       '加 `?v=1` 开始记' % (name, '、'.join(rec['none'])))
        if len(rec['v']) > 1:
            bad.append('`%s` 的版本号在各页之间**不一致**：%s —— 统一成一个（并重跑 --write）'
                       % (name, '、'.join('v=%d(%s)' % (v, ','.join(h))
                                          for v, h in sorted(rec['v'].items()))))
        elif rec['v']:
            v = list(rec['v'])[0]
            ent = man.get(name)
            if not ent:
                bad.append('`%s` **没登记在 `studio/web/_versions.json`**（v=%d）—— 跑 '
                           '`python scripts\\asset_versions.py --write`' % (name, v))
                continue
            if int(ent.get('v', -1)) != v:
                bad.append('`%s` html 写 v=%d，而清单记 v=%s —— 两侧不同步（跑 --write 或改 html）'
                           % (name, v, ent.get('v')))
            cur = sha16(path)
            if ent.get('sha') != cur:
                bad.append('`%s` **内容变了（sha %s→%s）但版本号还是 v=%d** —— 浏览器会继续用'
                           '缓存的旧文件：把 html 里的 `?v=%d` 改成 `?v=%d`，再跑 '
                           '`python scripts\\asset_versions.py --write`'
                           % (name, ent.get('sha'), cur, v, v, v + 1))
    return bad, {'refs': len(refs), 'n': n, 'manifest': len(man)}


def write():
    refs = scan_refs()
    man = {}
    for name, rec in sorted(refs.items()):
        path = os.path.join(WEB, name)
        if not os.path.isfile(path) or not rec['v'] or len(rec['v']) != 1:
            continue                      # 有问题的先不登记（--check 会报出来）
        man[name] = {'v': list(rec['v'])[0], 'sha': sha16(path)}
    man['_note'] = ('前端资源版本清单（生成物）：{文件: {v: html 里的 ?v=, sha: 内容 sha256 前 16 位}}。'
                    '改了 studio/web/*.js|*.css 要**先把 html 的 ?v= 加 1**，再跑 '
                    '`python scripts\\asset_versions.py --write` 刷新本文件；'
                    '守卫 `selftest.t_frontend_versions` 会核对这份清单。')
    with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write('\n')
    return man


def main():
    ap = argparse.ArgumentParser(description='前端资源版本清单（改了 js/css 必须升 ?v=）')
    ap.add_argument('--write', action='store_true', help='按当前 html 与文件内容刷新清单')
    ap.add_argument('--check', action='store_true', help='只检查（默认）')
    a = ap.parse_args()
    if a.write:
        man = write()
        print('已写出 %s：%d 个资源' % (os.path.relpath(MANIFEST, ROOT),
                                        len([k for k in man if not k.startswith('_')])))
        bad, st = problems()
        if bad:
            print('⚠ 仍有问题 %d 条：' % len(bad))
            for x in bad:
                print('   - %s' % x)
            return 1
        return 0
    # 走到这里 = **只检查**：`--check` 显式给，或**不带参数**（默认）。
    # ⚠ 必须**真的读** `a.check` —— 否则守卫 `dead_cli_args` 会报"声明了却没被读取"
    #   （本工具第一版就是这样被自检抓到的：`--check` 只是个装饰）。
    if not a.check:
        print('（不带参数 = 默认检查；要刷新清单用 --write）')
    bad, st = problems()
    print('前端资源：引用 %d 个资源 / %d 处 · 清单登记 %d 个'
          % (st['refs'], st['n'], st['manifest']))
    if st['n'] < MIN_REFS:
        print('✗ 只扫到 %d 处引用（少于下限 %d）—— 判据在空转，先修扫描口径' % (st['n'], MIN_REFS))
        return 1
    if bad:
        print('✗ %d 条问题：' % len(bad))
        for x in bad:
            print('   - %s' % x)
        return 1
    print('✓ 全部一致（内容 sha 与版本号对得上）')
    return 0


import cli_utf8 as _cu  # noqa: E402
_cu.setup()              # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
