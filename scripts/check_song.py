#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_song.py —— **渲染前**的单曲体检（省掉"渲染完才发现数据错"那一整轮）

为什么需要它：`make_song.py` 一轮 = 作曲 + 渲染 + 自动调参 ≈ 30~120 秒。而
`song.json` 是手写的，常见的错误（和弦名与音集不符、旋律强拍不在和弦音上、
轨名拼错、program 通道撞车、段落和弦数不符）**本来 2 秒就能查出来**。
实测一次血亏：写完 72 小节的歌直接渲染 → 自检 FAIL → 改数据 → 再渲染，
来回四轮才交付。

**判据不重复实现**：直接把项目自检的**全部检查项**跑在"只含这一首曲"的沙箱里
（`songs/_lint_<曲名>` 联到真曲目），所以规则永远跟 `selftest.py` 一致，
不会出现"两个工具的规则各说各话"。

用法:
  python scripts\\check_song.py 16_d150_bright_day        # 查一首（渲染前必跑）
  python scripts\\check_song.py --all                 # 查全部曲目
  python scripts\\check_song.py 16_d150_bright_day --fix   # 顺带修可自动修的（和弦音集/强拍）
  python scripts\\check_song.py 16_d150_bright_day --json  # 机器可读

退出码：0 = 可渲染；1 = 有问题（先修再渲染）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import json_io                                # noqa: E402
import selftest as st                         # noqa: E402  判据的唯一来源
import song_engine                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
PCN = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


# ------------------------------------------------------------------ 沙箱
def _link_or_copy(src, dst):
    try:
        os.symlink(src, dst, target_is_directory=os.path.isdir(src))
    except OSError:
        import shutil
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)


def lint_dirs(songs):
    """把指定曲目暴露成 `songs/` 下的一批目录（真曲目用符号链接，不复制产物）。

    selftest 的检查项全是"遍历 songs/*"，所以只要**改由沙箱提供曲目列表**，
    就能把整套判据原样用在单曲上。跑完即删。

    **仓库根的只读资源（refs/ docs/ studio/ 根 *.md）也要一起链进来**：判据里有一批是
    跨目录的（画像 `refs_schema`、文档指针 `docs_paths`、技能路由 `skill_routes_resolve`、
    文档分级 `docs_host_classification`）。沙箱里没有这些目录时，它们会一致地报"不存在" ——
    那是**沙箱造成的假报，不是数据问题**，却会在每次渲染前伪装成"未通过 N 项"红字，
    把真正的数据错误淹掉。
    """
    tmp_root = os.path.join(ROOT, '_lint_sandbox')
    songs_dir = os.path.join(tmp_root, 'songs')
    os.makedirs(songs_dir, exist_ok=True)
    for s in songs:
        dst = os.path.join(songs_dir, s)
        if not os.path.exists(dst):
            _link_or_copy(os.path.join(SONGS, s), dst)
    # 文档/画像资源用**复制**而不是软链：判据 `docs_host_classification` 会用 realpath
    # 判断文档"在不在仓库内"，软链会解析回真仓库 → 被误判成"在仓库外却没标宿主级"。
    # 这些资源都很小（refs 0.1MB、docs/、studio/ 0.3MB），复制无压力；曲目仍用软链（不复制产物）。
    import shutil
    for name in ('refs', 'docs', 'studio'):
        src, dst = os.path.join(ROOT, name), os.path.join(tmp_root, name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__'))
    for fn in os.listdir(ROOT):
        src, dst = os.path.join(ROOT, fn), os.path.join(tmp_root, fn)
        if fn.endswith('.md') and os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
    return tmp_root


def cleanup(tmp_root):
    import shutil
    shutil.rmtree(tmp_root, ignore_errors=True)


def _strict_downbeats(path):
    """单曲严判：**每个**强拍（第 1、3 拍）都必须落在该小节和弦音上。

    **为什么默认只警告、不作门（实测校准）**：库里既有曲目普遍有 7~15 处"强拍经过音"
    （如 `A 和弦小节强拍写 86=D`、`G 小节强拍写 81=A`），这是正常写法；项目判据
    （`selftest.melody_chord_fit`）因此定成**全库 ≥70%**。严判据 100% 是**新曲自我要求**，
    拿它当默认门会把 70% 的旧库全判死。所以：默认列出供参考，`--strict` 才拦住。
    """
    d = json_io.load(path)
    ch, mel = d['chords'], d['melody']
    bad = []
    for sec in d['sections']:
        arr = mel.get(sec.get('melody'))
        if not isinstance(arr, list):
            continue
        for bi, cname in enumerate(sec.get('chords', [])):
            if cname not in ch:
                continue
            tones = [int(t) % 12 for t in ch[cname][1]]
            root = int(ch[cname][0]) % 12
            for it in arr:
                if len(it) < 4 or it[0] != bi or it[1] not in (0.0, 2.0):
                    continue
                pc = int(it[3]) % 12
                if pc in tones:
                    continue
                # **和弦扩展音不算错音**：9 度(+2) 与 13 度(+9) 落在强拍是爵士/流行的常规写法
                # （倚音、延留音同理）。把它们当错音会逼出过度保守的旋律 —— 实测已发生：
                # F 三和弦上的 E(maj7)、Fsus4 上的 G(9)、A#m7 上的 D#(11) 都被误报过。
                # ♭9（根音上方 1 半音，最刺耳）与 11 度(+5，大三和弦上的 avoid note) 仍算错。
                if (pc - root) % 12 in (2, 9):
                    continue
                if pc not in tones:
                    bad.append('%s bar%d 拍%.0f: %d(%s) 不在 %s 的弦内音 [%s]'
                               % (sec['name'], bi + 1, it[1] + 1, it[3], PCN[int(it[3]) % 12],
                                  cname, ' '.join(PCN[t] for t in tones)))
    return bad


def _brief(items, head=5, tail=90):
    """一屏可读：只列前 head 条，其余折叠成计数（坏和弦会一口气带出几十条）"""
    if len(items) <= head:
        return '; '.join(items)
    s = '; '.join(items[:head])
    if len(s) > tail:
        s = s[:tail] + '…'
    return '%s … 共 %d 条' % (s, len(items))


# 沙箱里要跳过的自指/基准错位检查。**只在沙箱执行路径（run_checks_on）里生效**：
# 完整 selftest 照常跑这些项（那里 ROOT 就是真仓库，基准不会错位）。
#   check_song_sandbox        → 自指，会无限递归（实测把自检跑成 300s 超时）
#   docs_host_classification  → 它拿 `token_audit.DOCS` 里那些**模块级真实路径**去和
#                               `st.ROOT` 比前缀判断"在不在仓库内"；沙箱把 st.ROOT 换成
#                               沙箱目录后两个基准错位，必然假报"在仓库外却没标宿主级"。
#                               它是仓库级文档卫生检查，与某一首曲目的数据契约无关。
SKIP_SELF_CHECKS = ('check_song_sandbox', 'docs_host_classification')

# 沙箱里"空转"的**样本依赖**检查：失败消息带这些字样 = 样本不足，不是数据错误。
# 单曲沙箱只有 1 个 MIDI 样本，`midi_probe_all` 必然嫌少；`build_song_spec_roundtrip`
# 要的那首样本曲目在单曲模式下也不在沙箱里。它们在 `--all` 与完整自检里照常判定，
# 所以这里归入"跳过（不适用）"，不混进"未通过"里吓人。
VACUOUS_HINTS = ('数量太少', '会空转', '样本太少', '没有可比对的曲目')


def run_checks_on(tmp_root, only=None):
    """把 selftest 的检查项跑在沙箱上 → ([(检查名, 说明 or None)], [失败])

    `only=None` 跑全部（`--fast` 时只跑 `DATA_CHECKS`）。

    只改 `st.ROOT`（`song_dirs()` 从它推 songs/ 路径）。判据函数与 `CHECKS` 列表
    都是 selftest 的原件，所以规则永远同步、不会各说各话。

    **必须跳过自指检查**：`check_song_sandbox` 自己会再调 `run_checks_on` → 无限递归
    （实测直接把自检跑成 300s 超时）。同理跳过"格式兜底"这类与曲目无关的重活。
    """
    real_root = st.ROOT
    saved_fails = list(st.FAILS)
    st.ROOT = tmp_root
    st.FAILS = []
    results, fails = [], []
    try:
        for fn in st.CHECKS:
            name = fn.__name__[2:]
            if name in SKIP_SELF_CHECKS:
                continue
            if only is not None and name not in only:
                continue
            try:
                st.quiet(fn)
                results.append((name, None))
            except AssertionError as e:
                # 失败消息里带的曲名是沙箱外的真实名字，直接可用
                results.append((name, str(e)))
                fails.append((name, str(e)))
            except Exception as e:                      # noqa: BLE001
                msg = '%s: %s' % (type(e).__name__, e)
                results.append((name, msg))
                fails.append((name, msg))
    finally:
        st.ROOT = real_root
        st.FAILS = saved_fails
    return results, fails


# ------------------------------------------------------------------ 自动修
def _build_voicing(sym, lo=55):
    """按和弦符号造排列：音级**精确等于**符号，低音单独放低八度。

    （判据来自 selftest.parse_chord —— 与自检同一套；斜杠和弦的 '6/9' 不是斜杠）
    """
    base, slash = sym, None
    if '/' in sym and not sym.endswith('6/9'):
        base, sl = sym.split('/', 1)
        slash = st.NOTE_PC.get(sl)
    r, _, want = st.parse_chord(base)
    if want is None or r is None:
        return None
    notes, prev = [], lo - 1
    for pc in sorted(want, key=lambda x: (x - r) % 12):
        n = lo + ((pc - lo) % 12)
        while n <= prev:
            n += 12
        notes.append(n)
        prev = n
    bpc = slash if slash is not None else r
    bass = 28 + ((bpc - 28) % 12)
    if bass > 45:
        bass -= 12
    return [bass, notes]


def _fix_one_bar(ch, sec, bi, groups, cname, fixed):
    """修一个小节的强拍：先试换和弦品质（能覆盖全部强拍音），不行才逐个吸附到弦内音。

    提取出来的原因：这段是 `autofix` 里唯一有 5 层嵌套的部分，留在主流程里既难读
    也难单独验证（"为什么这行改了那个音"要翻半屏）。"""
    slab = []                                   # [(数组, 下标, 音高)]
    for arr in groups:
        if not isinstance(arr, list):
            continue
        for i, it in enumerate(arr):
            if len(it) >= 4 and it[0] == bi and it[1] in (0.0, 2.0):
                slab.append((arr, i, int(it[3])))
    if not slab:
        return
    tones = [int(t) for t in ch[cname][1]]
    tset = [t % 12 for t in tones]
    if all(m % 12 in tset for _a, _i, m in slab):
        return
    need = [m % 12 for _a, _i, m in slab]

    # ① 换和弦品质：以原和弦根音为准，找一个能覆盖全部强拍音的
    root = st.parse_chord(cname)[0]
    for q in ('', 'm', '7', 'maj7', 'm7', '6', 'm6', 'sus4', '7sus4', 'sus2',
              'add9', '9', 'maj9', 'm9'):
        cand = PCN[root] + q
        want = st.parse_chord(cand)[2]
        if want and all(n in want for n in need):
            nv = _build_voicing(cand)
            if nv:
                sec['chords'][bi] = cand
                ch[cand] = nv
                fixed.append('%s bar%d：和弦 %s → %s（同时覆盖强拍 %s）'
                             % (sec['name'], bi + 1, cname, cand,
                                ' '.join(PCN[n] for n in need)))
                return

    # ② 换不动 → 逐个吸附到最近的弦内音
    for arr, i, m in slab:
        cand = min(tones, key=lambda t: (abs(t - m), t < m))
        while abs(cand - m) > 6:
            cand += 12 if cand < m else -12
        if cand % 12 != m % 12:
            arr[i][3] = cand
            fixed.append('旋律 %s bar%d：%d(%s) → %d(%s)（%s 的弦内音）'
                         % (sec['name'], bi + 1, m, PCN[m % 12], cand,
                            PCN[cand % 12], cname))


def autofix(path):
    """修两类**可机械修正**的数据错：
      ① 和弦排列与符号不符 → 按符号重排（语义由符号定义，排列只是摆放）
      ② 旋律强拍不在该小节和弦音上 → 见 `_fix_one_bar`

    ②必须**按小节整体**处理。踩过的坑：逐音吸附会让同一小节里两个强拍各朝一边跑，
    改完仍然互相矛盾 —— 实测第一版 `--fix` 反而把小节改坏。
    其余问题（轨名/通道/越界）一律只报不猜 —— 猜错比不修更贵。
    """
    d = json_io.load(path)
    fixed = []
    ch, mel = d['chords'], d['melody']

    # ---- ① 和弦排列与符号不符
    for name in list(ch):
        v = ch[name]
        _, _, want = st.parse_chord(name)
        if want is None or not isinstance(v, (list, tuple)) or len(v) != 2:
            continue
        got = set(int(t) % 12 for t in v[1])
        if got != want:
            nv = _build_voicing(name)
            if nv:
                ch[name] = nv
                fixed.append('和弦 %s：音集 %s → 按符号重排为 %s'
                             % (name, ' '.join(PCN[t] for t in sorted(got)),
                                ' '.join(PCN[int(t) % 12] for t in nv[1])))

    # ---- ② 旋律强拍（按小节整体；原数组不能拷贝，否则改的是副本）
    for sec in d['sections']:
        groups = [mel.get(sec.get('melody'))]
        if isinstance(sec.get('melody_extra'), list):
            groups.append(sec['melody_extra'])
        for bi, cname in enumerate(sec.get('chords', [])):
            if cname in ch:
                _fix_one_bar(ch, sec, bi, groups, cname, fixed)
    if fixed:
        json_io.save(path, d)
    return fixed

# ------------------------------------------------------------------ 主流程
# 数据契约类检查名（渲染前必须先过的那批；也用于 check_song --fast）
DATA_CHECKS = ('song_json_buildable', 'style_unknown', 'bad_arr_key_warns',
               'voicing_shift', 'channels_and_programs', 'chord_names_match_notes',
               'melody_within_sections', 'style_desc_matches_programs',
               'unused_chords_warn', 'malformed_inputs', 'long_song_compose',
               'melody_chord_fit', 'section_mix_automation', 'song_json_canonical',
               'strict_downbeats')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    do_all = '--all' in sys.argv
    do_fix = '--fix' in sys.argv
    as_json = '--json' in sys.argv
    strict_mode = '--strict' in sys.argv
    fast = '--fast' in sys.argv      # 只跑数据契约批

    if do_all:
        songs = sorted(d for d in os.listdir(SONGS)
                       if os.path.isdir(os.path.join(SONGS, d))
                       and os.path.exists(os.path.join(SONGS, d, 'song.json')))
    elif args:
        songs = [args[0]]
    else:
        print(__doc__)
        return 1
    bad = [s for s in songs if not os.path.exists(os.path.join(SONGS, s, 'song.json'))]
    if bad:
        print('找不到曲目: %s\n可选: %s' % (', '.join(bad),
              ', '.join(sorted(os.listdir(SONGS)))))
        return 1

    fixed_total = []
    if do_fix:
        for s in songs:
            f = autofix(os.path.join(SONGS, s, 'song.json'))
            if f:
                fixed_total.append((s, f))

    tmp = lint_dirs(songs)
    try:
        results, fails = run_checks_on(tmp, only=(DATA_CHECKS if fast else None))
    finally:
        cleanup(tmp)

    # 单曲严判据（100%）：与自检的"全库 ≥70%"不同口径 —— 默认**只列**，

    # 编配干跑：**真的调一次引擎**（会写一遍 MIDI）拦"数据看着对、引擎一跑就崩"的错。
    # 实测教训：`arr.mix` 写成 {"Strings": [54, 76]}（段落自动化该收**单整数 CC7**）时，
    # 数据契约检查全绿、渲染到 write_midi 才 `int(list)` 崩 —— 白跑一轮。
    _compose_err = None
    try:
        import song_engine as _se
        _se.compose(os.path.join(SONGS, songs[0], 'song.json'))
    except Exception as _e:                                      # noqa: BLE001
        _compose_err = '%s: %s' % (type(_e).__name__, _e)
    results.append(('compose_dry_run', _compose_err))
    if _compose_err:
        fails.append(('compose_dry_run', _compose_err))

    # `--strict` 才当门（否则旧库那些正当的强拍经过音会被误判成错误）
    strict_all = []
    for s in songs:
        strict = _strict_downbeats(os.path.join(SONGS, s, 'song.json'))
        if strict:
            strict_all.append((s, _brief(strict)))
            results.append(('strict_downbeats', _brief(strict)))
            if strict_mode:
                fails.append(('strict_downbeats', _brief(strict)))

    # 样本不足导致的"空转"先摘出去：它们不是本曲的数据问题，也不该显示成"未通过"
    vacuous = [(n, m) for n, m in fails if any(h in m for h in VACUOUS_HINTS)]
    fails = [(n, m) for n, m in fails if not any(h in m for h in VACUOUS_HINTS)]

    data_fails = [(n, m) for n, m in fails if n in DATA_CHECKS]
    other_fails = [(n, m) for n, m in fails if n not in DATA_CHECKS]

    if as_json:
        print(json.dumps({'songs': songs, 'fixed': fixed_total,
                          'fails': fails, 'data_fails': data_fails,
                          'checked': len(results)}, ensure_ascii=False, indent=1))
        return 1 if data_fails else 0

    print('== check_song：%s（复用了自检的 %d 项判据）==' % (', '.join(songs), len(results)))
    if fixed_total:
        print('自动修（--fix）:')
        for s, f in fixed_total:
            for line in f:
                print('  ✓ %s' % line)
        print('  改的是 song.json，记得**重渲染**（make_song.py 不加 --no-compose）')
    if strict_all and not strict_mode:
        print('提示（强拍经过音，**不是错误**）：')
        for s, msg in strict_all:
            print('  · %s: %s' % (s, msg))
        print('  项目判据是"强拍弦内音 ≥70%%"（自检 melody_chord_fit）；100%% 是我方的自我要求，'
              '加 --strict 才会当门')
    if vacuous:
        print('跳过（%d 项，**沙箱样本不足 → 本曲不适用**；完整 selftest 或 --all 会判定）：'
              % len(vacuous))
        for n, m in vacuous:
            print('  · %s → %s' % (n, m))
    if fails:
        print('未通过（%d 项）：' % len(fails))
        for n, m in fails:
            tag = '数据契约' if n in DATA_CHECKS else '其它'
            print('  - [%s] %s → %s' % (tag, n, m))
    else:
        print('  ✓ 全部通过，可以渲染：make_song.py %s' % songs[0])
    print('\n结论：%s' % ('**先修数据再渲染**（上面标"数据契约"的先看）' if data_fails
                       else '数据契约没问题，可以渲染'))
    if other_fails:
        print('      （其余 %d 项：%s —— 多为交付物/跨曲目检查，不影响本次渲染）'
              % (len(other_fails), ', '.join(n for n, _ in other_fails[:6])))
    return 1 if data_fails else 0


if __name__ == '__main__':
    sys.exit(main())
