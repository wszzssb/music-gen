#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测可用的 GitHub 加速镜像（release 资源 + raw 文件）"""
import urllib.request

FS_PATH = ('FluidSynth/fluidsynth/releases/download/v2.6.0/'
           'fluidsynth-v2.6.0-win10-x64-cpp11.zip')
SF_PATH = 'ROCKNIX/generaluser-gs/main/GeneralUser%20GS%20v1.471.sf2'

PROXIES = [
    'https://ghproxy.net/https://github.com/%s',
    'https://gh-proxy.com/https://github.com/%s',
    'https://ghfast.top/https://github.com/%s',
    'https://gh.llkk.cc/https://github.com/%s',
    'https://github.moeyy.xyz/https://github.com/%s',
    'https://ghproxy.cc/https://github.com/%s',
    'https://cdn.jsdelivr.net/gh/%s',
    'https://raw.gitmirror.com/%s',
    'https://gcore.jsdelivr.net/gh/%s',
]


def probe(url, label):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                                   'Range': 'bytes=0-1023'})
        with urllib.request.urlopen(req, timeout=15) as f:
            d = f.read(64)
            cr = f.headers.get('Content-Range') or f.headers.get('Content-Length')
            print('  OK   %-58s %s  head=%r' % (label, cr, d[:4]))
            return True
    except Exception as e:
        print('  FAIL %-58s %s' % (label, str(e)[:52]))
        return False


def main():
    for name, path in (('FluidSynth', FS_PATH), ('SoundFont', SF_PATH)):
        print('== %s ==' % name)
        for p in PROXIES:
            probe(p % path, p.split('/')[2])
        print()


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
