"""控制台编码兜底：让工具链在**任何控制台/任何工作目录**都能跑。

Windows 默认控制台是 GBK（cp936），脚本一旦打印 `✓`（U+2713）这类不在 GBK 里的字符，
就会 `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'` —— 直接在
自动调参中途崩掉。以前没暴露是因为本地跑之前都设了 `chcp 65001` / `PYTHONIOENCODING=utf-8`；
换个目录、换台机器、换一个对话就必崩。

各入口脚本顶层调用 `setup()`（放在 `if __name__ == '__main__'` 之前，import 时即生效），
把 stdout/stderr 切成 UTF-8 + errors='replace'：编码不了的字降级成 `?`，**绝不因为
一个装饰性字符中断渲染/调参**。

`setup()` 顺带提供**统一的 `--help` 出口**：任意入口脚本带 `--help` 都打印它自己的
docstring 后以 0 退出，而不是"忽略未知参数、直接开跑"。实测此前 `rehearsal.py --help`
会直接跑完整套彩排（`make_song.py --help` 会直接开始作曲渲染）—— 对刚上手（或刚 clone
下来）的人来说，第一条命令就是 `--help`，那样等于踩雷。用 argparse 的那几个脚本自带帮助，
这里先拦一道，输出同样是文件 docstring（工具链的用法说明本来就写在 docstring 里）。
"""

import os
import sys


def setup():
    """把标准输出/错误切成 UTF-8（幂等、失败静默）；顺带处理 `--help`。

    **必须在打印任何东西之前调用**：编码先切换，帮助文本才输出得安全。
    """
    for s in (getattr(sys, 'stdout', None), getattr(sys, 'stderr', None)):
        try:
            enc = (getattr(s, 'encoding', '') or '').lower().replace('-', '')
            if enc == 'utf8':
                continue
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if '--help' in sys.argv[1:]:
        _print_help()


def _print_help():
    """打印调用脚本自己的 docstring（工具链的用法说明就写在那里）后以 0 退出。"""
    m = sys.modules.get('__main__')
    doc = (getattr(m, '__doc__', '') or '').strip()
    path = getattr(m, '__file__', '') or (sys.argv[0] if sys.argv else '')
    name = os.path.basename(path) if path else 'tool'
    print('=' * 72)
    if doc:
        print(doc)
    else:
        print('%s —— 这个脚本没写 docstring（工具链约定：用法写在文件开头）' % name)
    print('=' * 72)
    print('提示：命令清单见 CHEATSHEET.md，工具总索引见 README.md。')
    raise SystemExit(0)
