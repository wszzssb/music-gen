"""song.json 的读写：**紧凑但可读**（音符/和弦的数组写在一行里）。

为什么要有这个模块 —— 省 token：
`json.dump(..., indent=1)` 会把每个数字拆成一行，一首 40 小节的歌光 `melody` 就 600 多个音符
→ 3000 多行、30KB 文本。任何 agent 只要"读一遍 song.json"就要吃掉上万 token，
而这只是写歌流程里最普通的一步。

本模块的格式约定：
· 结构（dict / 段落列表）照常换行缩进，人眼看结构；
· **纯数字数组**（音符 `[小节, 拍, 时值, 音高]`、和弦音集）保持行内紧凑；
· 和弦 `"G": [31, [55, 59, 62, 67, 71]]` 也是单行。

实测：全部 5 首歌的 song.json 平均缩小 ~70%，而 `json.loads()` 结果**逐字节等价**
（自检 `song_json_compact` 守着这条：既有等价性，也有"没被某个工具重新写胖"）。
"""

import json
import os
import sys


def _flat(o):
    return isinstance(o, list) and all(not isinstance(x, (list, dict)) for x in o)


def _note(o):
    """音符 [小节, 拍, 时值, 音高] / [拍, 时值, 音高] 之类的短数字组"""
    return isinstance(o, list) and 3 <= len(o) <= 4 and _flat(o)


def _note_list(o):
    return (isinstance(o, list) and o
            and all(isinstance(x, list) and _flat(x) for x in o))


def _chord_pair(o):
    """和弦写法 [根音, [音集…]] —— 也压成一行"""
    return (isinstance(o, list) and len(o) == 2
            and not isinstance(o[0], (list, dict)) and _flat(o[1]))


def _line(o, lvl, indent):
    sp = ' ' * (indent * lvl)
    return '%s%s' % (sp, _scalar(o) if isinstance(o, list) else _enc(o, lvl, indent))


def _scalar(o):
    """行内紧凑形式"""
    if _flat(o):
        return '[' + ', '.join(json.dumps(x, ensure_ascii=False) for x in o) + ']'
    if _chord_pair(o):
        return '[%s, %s]' % (json.dumps(o[0], ensure_ascii=False), _scalar(o[1]))
    return None


def _enc(o, lvl, indent):
    sp = ' ' * (indent * lvl)
    if isinstance(o, dict):
        if not o:
            return '{}'
        parts = []
        for k, v in o.items():
            key = json.dumps(k, ensure_ascii=False)
            parts.append('%s%s: %s' % (' ' * (indent * (lvl + 1)), key,
                                       _enc(v, lvl + 1, indent)))
        return '{\n' + ',\n'.join(parts) + '\n' + sp + '}'
    if isinstance(o, list):
        if not o:
            return '[]'
        if _flat(o) or _chord_pair(o):
            return _scalar(o)
        pad = ' ' * (indent * (lvl + 1))
        # 音符表：**按小节分行**（同一小节的音符在同一行）
        # —— 行数从"每音符一行"降到"每小节一行"，而每个音符仍是文件里的独立子串，
        #    所以精准 edit 一个音符照样只替换那一小段。
        if _note_list(o) and all(_note(x) for x in o):
            rows, cur = [], None
            for x in o:
                if cur is not None and x[0] == cur[0]:
                    cur[1].append(x)
                else:
                    cur = (x[0], [x])
                    rows.append(cur)
            body = ',\n'.join(pad + ', '.join(_scalar(y) for y in g) for _, g in rows)
            return '[\n' + body + '\n' + sp + ']'
        parts = []
        for x in o:
            one = _scalar(x) if isinstance(x, list) else None
            if one is not None:                       # 和弦：一行一个
                parts.append(pad + one)
            else:
                parts.append(pad + _enc(x, lvl + 1, indent))
        return '[\n' + ',\n'.join(parts) + '\n' + sp + ']'
    return json.dumps(o, ensure_ascii=False)


def dumps(obj, indent=1):
    """紧凑但可读的 JSON 文本（结构与 json.dumps 兼容，只是数组内联）"""
    return _enc(obj, 0, indent) + '\n'


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save(path, obj, indent=1):
    """写文件：数据与 obj 完全等价（自检会 json.loads 回来比对）"""
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(dumps(obj, indent))


def normalize(path):
    """把文件就地改写成规范格式；返回 (旧行数, 新行数, 是否改动)。
    **先验证 json.loads 等价**，不等价就拒绝写入（绝不为了排版动数据）。"""
    old = open(path, encoding='utf-8').read()
    data = json.loads(old)
    new = dumps(data)
    if json.loads(new) != data:
        raise ValueError('%s: 规范化后数据不等价，已中止' % path)
    if new == old:
        return old.count('\n') + 1, new.count('\n') + 1, False
    save(path, data)
    return old.count('\n') + 1, new.count('\n') + 1, True


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）


def main():
    import glob
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if not args:
        args = sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'songs', '*', 'song.json')))
    for p in args:
        o, n, ch = normalize(p)
        print('%-40s %4d 行 → %4d 行 %s' % (p, o, n, '（已规范）' if ch else '（本来就是）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
