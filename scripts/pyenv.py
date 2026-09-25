# -*- coding: utf-8 -*-
"""解释器兜底：有的工具**必须在另一个 venv 里跑**，而文档与 `__doc__` 写的都是
`python scripts/xxx.py` —— 实测按文档跑会崩在半路，且 traceback 指向第三方库内部、
看着像那个库坏了。

为什么需要它（2026-09-25 实测两条，都是"文档与实现不一致"）：

| 工具 | 现象 | 真因 |
|---|---|---|
| `transcribe_audit.py` | `python scripts/transcribe_audit.py …`（主 venv）→ traceback 停在 `librosa/__init__` → `scipy._lib._ccallback` → `_ccallback_c` | 它把 `.venv-ml/Lib/site-packages` **插进 sys.path** 再 import librosa；主 venv 是 **Python 3.14**、而 scipy 是 **cp313 轮子** → 编译扩展 ABI 对不上 |
| `bp_transcribe.py` | 主 venv 跑 → `ModuleNotFoundError: No module named 'basic_pitch'` | `__doc__` 写着"独立 bp-venv（`BP_PY` 可覆盖）"，但代码用的是 `sys.executable`，**BP_PY 从未被用于切换解释器** |

用法（**必须放在 import 目标库之前**）：

```python
import pyenv
pyenv.ensure('librosa', '.venv-ml', '精度体检要 librosa（装在 .venv-ml）')
import librosa            # 到这里一定可导入
```

`venv` 可以是**相对仓库根的名字**（`.venv-ml`）、**绝对目录**、或**直接给解释器路径**。

行为（三条，无隐式降级）：
  ① 当前解释器**能 import**目标模块 → 立刻返回，零副作用；
  ② 不能、但目标环境有解释器 → `os.execv` **换成它重跑本脚本**（`sys.argv` 原样透传，
     带防重入标志 `DSH_PYENV_REEXEC`，不会自己套自己）；
  ③ 都不能 → 打印**可复制的正确命令**并 `exit(3)`，不抛 traceback。
"""
import importlib
import os
import sys

_FLAG = 'DSH_PYENV_REEXEC'


def _importable(name):
    """真 import 一次 —— 不用 `find_spec`：实测"能找到但加载崩"（scipy 那种）正是本例主因。"""
    try:
        importlib.import_module(name)
        return True
    except BaseException:                                          # noqa: BLE001
        return False


def _python_in(target):
    """→ 目标环境的 python 解释器路径（target 可已是解释器本身）。"""
    if os.path.isfile(target) and os.path.basename(target).lower().startswith('python'):
        return target
    for rel in (os.path.join('Scripts', 'python.exe'), os.path.join('bin', 'python3'),
                os.path.join('bin', 'python')):
        p = os.path.join(target, rel)
        if os.path.isfile(p):
            return p
    return None


def ensure(module, venv, why='', extra_hint=''):
    """见模块 docstring。返回 True = 当前解释器可用（②会 execv，不返回）。"""
    if _importable(module):
        return True
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = venv if os.path.isabs(venv) else os.path.join(root, venv)
    py = _python_in(target)
    script = os.path.abspath(sys.argv[0] or '')
    if py and os.environ.get(_FLAG) != py:
        os.environ[_FLAG] = py                    # 防重入：目标环境里再失败就直接报错
        print('[pyenv] 当前解释器没有可用的 %s → 换成 %s 重跑' % (module, py), flush=True)
        os.execv(py, [py, script] + sys.argv[1:])
        raise SystemExit(3)                       # execv 正常不返回；真返回了也不能继续
    print('✗ 找不到可用的 %s：%s' % (module, why or '本工具需要它'))
    print('  当前解释器：%s' % sys.executable)
    print('  目标环境  ：%s（%s）' % (target, '有解释器' if py else '**没有解释器**'))
    if py:
        print('  正确命令  ："%s" "%s"%s'
              % (py, script, ''.join(' "%s"' % x for x in sys.argv[1:])))
        print('  装法      ："%s" -m pip install <包名>' % py)
    if extra_hint:
        print('  ' + extra_hint)
    sys.exit(3)
