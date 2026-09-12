#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并准备真音源渲染环境：FluidSynth(win64) + GeneralUser GS 音源

产出:
  tools/fluidsynth/   FluidSynth 可执行文件与依赖 DLL
  tools/GeneralUser GS v1.471.sf2

用法: python setup_soundfont.py
"""
import os
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'vendor')

FS_URLS = [
    'https://ghproxy.net/https://github.com/FluidSynth/fluidsynth/releases/download/'
    'v2.6.0/fluidsynth-v2.6.0-win10-x64-cpp11.zip',
    'https://gh-proxy.com/https://github.com/FluidSynth/fluidsynth/releases/download/'
    'v2.6.0/fluidsynth-v2.6.0-win10-x64-cpp11.zip',
    'https://ghfast.top/https://github.com/FluidSynth/fluidsynth/releases/download/'
    'v2.6.0/fluidsynth-v2.6.0-win10-x64-cpp11.zip',
    'https://github.com/FluidSynth/fluidsynth/releases/download/'
    'v2.6.0/fluidsynth-v2.6.0-win10-x64-cpp11.zip',
]
SF_URLS = [
    'https://raw.githubusercontent.com/ROCKNIX/generaluser-gs/main/'
    'GeneralUser%20GS%20v1.471.sf2',
    'https://cdn.jsdelivr.net/gh/ROCKNIX/generaluser-gs@main/'
    'GeneralUser%20GS%20v1.471.sf2',
]
SF_NAME = 'GeneralUser GS v1.471.sf2'


def fetch(url, dst, label, attempts=6):
    """带重试与断点续传的下载（GitHub release 直连偶尔会被重置）"""
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        print('  已存在，跳过: %s (%.1fMB)' % (label, os.path.getsize(dst) / 1048576))
        return dst
    part = dst + '.part'
    for k in range(1, attempts + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {'User-Agent': 'Mozilla/5.0'}
        if have:
            headers['Range'] = 'bytes=%d-' % have
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get('Content-Length') or 0) + have
                if have and r.status != 206:
                    have = 0                     # 服务器不支持续传，重头来
                    part = dst + '.part'
                mode = 'ab' if have else 'wb'
                got = have
                last = -1
                with open(part, mode) as f:
                    while True:
                        b = r.read(65536)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        pct = int(100 * got / total) if total else 0
                        if pct != last and pct % 10 == 0:
                            print('    %3d%%  %.1f/%.1fMB' %
                                  (pct, got / 1048576, total / 1048576), flush=True)
                            last = pct
            if total and os.path.getsize(part) < total:
                print('  第%d次未下完（%d/%d），续传' %
                      (k, os.path.getsize(part), total))
                continue
            os.replace(part, dst)
            print('  完成 %s (%.1fMB)' % (label, os.path.getsize(dst) / 1048576))
            return dst
        except Exception as e:
            print('  第%d次失败: %s' % (k, str(e)[:70]))
            if k == attempts:
                raise
    return dst


def fetch_any(urls, dst, label, attempts=4):
    """多镜像回退下载（国内直连 GitHub 不稳定）"""
    last = None
    for u in urls:
        try:
            print('  源:', u.split('/')[2])
            return fetch(u, dst, label, attempts)
        except Exception as e:
            last = e
            print('  该源失败: %s' % str(e)[:60])
    raise last


def main():
    os.makedirs(TOOLS, exist_ok=True)
    print('[1/3] FluidSynth')
    zpath = os.path.join(TOOLS, 'fluidsynth-win64.zip')
    fetch_any(FS_URLS, zpath, 'FluidSynth 2.6.0 win64')
    dest = os.path.join(TOOLS, 'fluidsynth')
    if not os.path.exists(os.path.join(dest, 'bin', 'fluidsynth.exe')):
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dest)
        print('  解压到', dest)
    exe = None
    for root, _, files in os.walk(dest):
        if 'fluidsynth.exe' in files:
            exe = os.path.join(root, 'fluidsynth.exe')
            break
    print('  可执行文件:', exe)

    print('[2/3] GeneralUser GS 音源')
    sf = fetch_any(SF_URLS, os.path.join(TOOLS, SF_NAME), 'GeneralUser GS 1.471')
    with open(sf, 'rb') as f:
        print('  文件头:', f.read(4))

    print('[3/3] 完成')
    print('  FLUIDSYNTH=%s' % exe)
    print('  SOUNDFONT=%s' % sf)
    return 0 if exe else 1


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
