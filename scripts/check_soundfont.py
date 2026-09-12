#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测免费 GM 音源的可下载地址（为后续用 fluidsynth 离线渲染 MIDI 做准备）"""
import json
import re
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0'}


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read()


def main():
    print('== GeneralUser GS 页面上的下载链接 ==')
    try:
        html = get('https://schristiancollins.com/generaluser.php').decode('utf-8', 'replace')
        links = set(re.findall(r'href=["\']([^"\']+\.(?:zip|sf2|7z|rar))["\']', html, re.I))
        for l in sorted(links):
            print('  ', l if l.startswith('http') else 'https://schristiancollins.com/' + l.lstrip('/'))
    except Exception as e:
        print('  失败:', e)

    print('== MuseScore 自带 MuseScore_General.sf3（免费，约 40MB）==')
    try:
        d = json.loads(get('https://api.github.com/repos/musescore/MuseScore/releases/latest'))
        print('   最新版本', d['tag_name'])
        for a in d['assets']:
            if a['name'].lower().endswith(('.mscz', '.zip', '.sf3')):
                print('   ', a['name'], round(a['size'] / 1048576, 1), 'MB')
    except Exception as e:
        print('   失败:', e)

    print('== fluidsynth 已确认可用 ==')
    print('   https://github.com/FluidSynth/fluidsynth/releases/download/v2.6.0/'
          'fluidsynth-v2.6.0-win10-x64-cpp11.zip  (2.6MB)')


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
