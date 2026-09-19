# -*- coding: utf-8 -*-
"""面板 i18n 覆盖率检查 —— 防"加了按钮忘了加字典"。

背景（为什么要有它）：中英切换实现成"**中文就是 HTML 原文** + 按字典替换"
（`studio/web/i18n.js`）。新增一个中文按钮却忘了往 DICT 里加，切英文时那一处
仍是中文 —— 而且**只有英文环境看得见**：中文系统下怎么点都不会暴露，
`smoke_ui.js` / `browser_check.js` 也照样全绿（它们跑在中文语言下）。

所以把它做成脚本：抓 `studio/web/index.html`、`ed.html` 里所有**含汉字**的文本节点
与 `title` / `placeholder`，逐条比对 i18n.js 的 DICT（外加 REGEX 表，
用于 `0.0 / 0.0 拍` 这种运行时才填上数值的文本）。

    .venv/Scripts/python.exe scripts/i18n_check.py       # 退出码 0 = 全覆盖
    .venv/Scripts/python.exe scripts/i18n_check.py -v    # 连"已覆盖"也逐条列出

退出码：0 = 全覆盖；1 = 有漏项（漏项逐条打印）。
"""
import io
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), 'studio', 'web')
ATTRS = ('title', 'placeholder')


def dict_entries(path):
    """返回 (DICT 键集合, REGEX 模式列表)。只解析 DICT/REGEX 两个块的字面量。"""
    src = io.open(path, encoding='utf-8').read()
    i = src.index('var DICT = {')
    j = src.index('\n  };', i)
    body = src[i:j]
    # 键后面紧跟冒号；值后面是逗号或换行，不会被这条正则捞进来
    # ⚠ 这里**故意不做 html.unescape**：字典的键是拿去和 `getAttribute()` 的返回值 /
    # 文本节点的 nodeValue 比对的，而那两个拿到的**已经是解码后的文本**（`&lt;` → `<`）。
    # 早先在这步 unescape 过，结果字典里写 `&lt;` 也算"通过检查"，运行时却匹配不上
    # —— 那几条 title 一直没翻，检查脚本反倒把 bug 藏住了。
    keys = set(k.replace("\\'", "'")
               for k in re.findall(r"'((?:[^'\\]|\\.)*)'\s*:", body))
    pats = []
    m = re.search(r'var REGEX = \[(.*?)\n  \];', src, re.S)
    if m:
        for p in re.findall(r'/\^(.*?)\$/', m.group(1)):
            pats.append(re.compile('^' + p + '$'))
    return keys, pats


class Catcher(HTMLParser):
    """抓"用户能看到的文本"：文本节点 + title/placeholder；script/style/注释不算。"""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.skip = 0
        self.texts = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        for k, v in attrs:
            if k in ATTRS and v:
                self.attrs.append(v.strip())

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if self.skip:
            return
        d = d.strip()
        if d:
            self.texts.append(d)


def has_cjk(s):
    return any('\u4e00' <= c <= '\u9fff' for c in s)


def main():
    verbose = '-v' in sys.argv or '--verbose' in sys.argv
    keys, pats = dict_entries(os.path.join(WEB, 'i18n.js'))
    total = miss_all = 0
    seen = set()
    for name in ('index.html', 'ed.html'):
        path = os.path.join(WEB, name)
        c = Catcher()
        c.feed(io.open(path, encoding='utf-8').read())
        # 去重但保序：同一句在文本与 title 里都出现时只报一次
        uniq = []
        for t in c.texts + c.attrs:
            if has_cjk(t) and t not in uniq:
                uniq.append(t)
        seen.update(uniq)
        miss = [t for t in uniq
                if t not in keys and not any(p.match(t) for p in pats)]
        total += len(uniq)
        miss_all += len(miss)
        print('== %s：含中文文案 %d 条，字典覆盖 %d 条，缺 %d 条'
              % (name, len(uniq), len(uniq) - len(miss), len(miss)))
        for t in miss:
            print('   MISS %r' % (t,))
        if verbose:
            for t in uniq:
                if t not in miss:
                    print('   ok   %r' % (t,))
    # 反向：字典里 HTML 已经不用的条目（改了 HTML 忘删字典）——只提示，不算失败
    dead = sorted(k for k in keys if k not in seen)
    if dead:
        print('-- 字典里 %d 条在两个 HTML 里都找不到（可能已改文案、忘删字典）：' % len(dead))
        for k in dead:
            print('   dead %r' % (k,))
    print('---- 合计 %d 条文案，缺 %d 条' % (total, miss_all))
    return 1 if miss_all else 0


if __name__ == '__main__':
    import cli_utf8
    cli_utf8.setup()      # 控制台编码兜底（GBK 下打印中文会崩）—— 与其它入口脚本一致
    sys.exit(main())
