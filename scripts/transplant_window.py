#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""transplant_window.py —— 把**参照版**某一窗的内容搬到**目标曲**上（**分段移植**，不是整曲替换）。

## 什么时候用

用户认可版就是基准（`SKILL` §9c/9d）。当某个局部（例：19.0–19.6s 的十六分连打）在新流程里
做不出来、而在参照版里已经对了 —— 与其整曲回退，不如**只搬这一窗**。

实测背景（BGM35，2026-10-06）：新流程 v2 在该窗只有 3 短音 + 4 长音堆叠，而用户认可版是
**6 粒十六分下行连打**（`A♯5 A5 F5 C5 A♯4 A4 F4`，每音 0.10s）。三种自动判据 ——
逐点最强峰 / 频谱新颖度 / DP 全局轨迹 —— **全都定不出这条轨迹**（每个网格点上能量最强的是
**持续音** E5；那条线从来不是能量主导）⇒ 与 `PITFALLS` 325 同族：混音级证据不能定归属。
所以对这类"局部已有人工认可答案"的情形，**移植是唯一可靠的路径**。

## 三条纪律

1. **分段**：只改 `--at` 窗内的**指定轨**，窗外一律不动（本工具逐音核对，并留备份 + SHA256）；
2. **bpm 必须一致**：`notes_extra` 的时间轴是二维的（小节, 拍）；两曲 bpm 不同则同一 (小节,拍)
   落在不同时刻 ⇒ **直接报错**，不做隐式时间拉伸（拉伸是另一件事，要单独举证）；
3. **参照版只读、目标版不改**：产物写到新曲目 `<目标>_tx`（`--name` 可改），原目录不动。

## 用法

```powershell
$py scripts\transplant_window.py <参照曲> <目标曲> --at 19.0-19.6 [--tracks Strings]
#   可选：--name <新曲名> · --no-render · --ab-dir <片段目录> · --audio <原曲音频(做A/B)>
$py scripts\transplant_window.py --selftest        # 纯函数自检（不碰文件）
```

产物：`songs\<新曲名>\`（song.json / .mid / _sf.ogg / render.json）+ A/B 片段
`<ab-dir>\0_原曲.ogg · A_参照版.ogg · B_移植前.ogg · C_移植后.ogg`。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, "songs")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
import cli_utf8 as _cu; _cu.setup()          # noqa: E402  GBK 控制台下打印 ✓ 会崩


# ── 纯逻辑（可自检，不碰文件系统）────────────────────────────────────
def notes_of(d, track):
    """取某轨的音符列表（兼容 `{"notes": [...]}` 与裸列表两种写法）。"""
    ne = (d.get("notes_extra") or {}).get(track)
    if isinstance(ne, dict):
        return list(ne.get("notes") or [])
    return list(ne or [])


def set_notes(d, track, notes):
    ne = d.setdefault("notes_extra", {})
    cur = ne.get(track)
    if isinstance(cur, dict):
        cur["notes"] = notes
    else:
        ne[track] = notes


def in_window(bar, beat, spb, t0, t1):
    """该音符的起始秒是否落在 [t0, t1]（用 (小节,拍) → 秒换算；0 基小节、小节内拍）。"""
    t = (bar * 4 + beat) * spb
    return t0 <= t <= t1


def transplant_dicts(ref, dst, tracks, t0, t1, verbose=True):
    """把 `ref` 在窗内的 `tracks` 音符搬到 `dst`（返回新 dst 的深拷贝 + 明细）。

    返回 `(new_dst, report)`；`report` 里每轨含 `removed` / `added` / `kept` 三个列表。
    bpm 不一致或窗内没有任何可搬内容 → `raise SystemExit`（**不静默**）。
    """
    spb_ref = 60.0 / float(ref.get("bpm") or 0)
    spb_dst = 60.0 / float(dst.get("bpm") or 0)
    if not spb_ref or not spb_dst:
        raise SystemExit("两首曲子都必须有 bpm（song.json 里没有？）")
    if abs(spb_ref - spb_dst) > 1e-6:
        raise SystemExit(
            "bpm 不一致（参照 %.3f vs 目标 %.3f）：`notes_extra` 是（小节,拍）坐标，"
            "同一坐标在两曲落在不同时刻 ⇒ 拒绝隐式拉伸。做法：先让两曲 bpm 一致，"
            "或改用逐音时间对齐的另一条工序。" % (ref.get("bpm"), dst.get("bpm")))

    import copy
    out = copy.deepcopy(dst)
    rep = {}
    for tr in tracks:
        mine = notes_of(dst, tr)
        theirs = [n for n in notes_of(ref, tr) if in_window(n[0], n[1], spb_ref, t0, t1)]
        removed = [n for n in mine if in_window(n[0], n[1], spb_dst, t0, t1)]
        kept = [n for n in mine if not in_window(n[0], n[1], spb_dst, t0, t1)]
        if not theirs:
            # ⚠ 参照版在该窗**没有内容** ⇒ 一律跳过：**不许把目标版这段清空**。
            #   （第一版就是这里静默删音 —— 自检的"窗内无可搬内容必须报错"当场抓到。）
            rep[tr] = {"removed": [], "added": [], "kept": mine, "skipped": True}
            if verbose:
                print("  [%s] 参照版该窗无内容 ⇒ 跳过（不清空目标版）" % tr)
            continue
        set_notes(out, tr, sorted(kept + theirs, key=lambda x: (x[0], x[1], x[3])))
        rep[tr] = {"removed": removed, "added": theirs, "kept": kept, "skipped": False}
        if verbose:
            print("  [%s] 搬入 %d 条 ← 参照版；替换掉 %d 条；窗外保持 %d 条"
                  % (tr, len(theirs), len(removed), len(kept)))
            for n in theirs:
                print("      + %s" % (n,))
            for n in removed:
                print("      - %s" % (n,))
    if all(v.get("skipped") for v in rep.values()):
        raise SystemExit("窗内没有任何可搬的内容（参照版那几轨在该窗为空？检查 --at / --tracks）")
    pats = out.setdefault("patterns", {})
    pats["transplant_window"] = (
        "%.2f–%.2fs 的 %s 轨内容从参照版 `%s` 移植（逐音，含时值/音高/力度）；"
        "窗外未动。工具 scripts/transplant_window.py"
        % (t0, t1, ",".join(tracks), ref.get("name") or "?"))
    return out, rep


def sha16(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def _resolve(name_or_path):
    """曲名或 song.json 路径 → (song.json 路径, 曲目目录)。"""
    p = name_or_path
    if os.path.isdir(p):
        p = os.path.join(p, "song.json")
    if not os.path.isfile(p):
        p = os.path.join(SONGS, name_or_path, "song.json")
    if not os.path.isfile(p):
        raise SystemExit("找不到 song.json：%s" % name_or_path)
    return p, os.path.dirname(p)


def selftest():
    """纯函数自检：窗判定 · 只动指定轨 · 窗外逐音不动 · bpm 不一致必须报错。"""
    ok = True

    def rep(name, cond, extra=""):
        nonlocal ok
        print("  %s %-46s %s" % ("PASS" if cond else "FAIL", name, extra))
        ok = ok and cond

    spb = 60.0 / 120.0
    rep("in_window：边界含端点", in_window(0, 0.0, spb, 0.0, 10.0)
        and in_window(0, 4.0, spb, 0.0, 2.0) and not in_window(0, 4.1, spb, 0.0, 2.0),
        "(小节0拍0 … 小节1拍0)")
    rep("in_window：窗外为假", not in_window(2, 0.0, spb, 0.0, 2.0))

    def mk(name, notes_strings, notes_piano):
        return {"name": name, "bpm": 120.0, "notes_extra": {
            "Strings": [list(x) for x in notes_strings],
            "Piano": [list(x) for x in notes_piano]}}

    ref = mk("ref", [[0, 0.5, 0.25, 80, 100], [0, 0.75, 0.25, 79, 100],
                     [3, 0.0, 1.0, 60, 90]], [[0, 0.5, 0.25, 55, 100]])
    dst = mk("dst", [[0, 0.5, 0.5, 70, 100], [3, 0.0, 1.0, 60, 90]],
             [[0, 0.5, 0.25, 55, 100]])
    out, r = transplant_dicts(ref, dst, ["Strings"], 0.0, 1.0, verbose=False)
    s_out = notes_of(out, "Strings")
    p_out = notes_of(out, "Piano")
    rep("搬入参照版的 2 条（窗内）", len([n for n in s_out if n[0] == 0]) == 2,
        "%s" % [n for n in s_out if n[0] == 0])
    rep("窗外（小节 3）逐音不动", [n for n in s_out if n[0] == 3] == [[3, 0.0, 1.0, 60, 90]])
    rep("非指定轨（Piano）逐音不动", p_out == notes_of(dst, "Piano"), "%s" % p_out)
    rep("原目标对象未被就地修改", len(notes_of(dst, "Strings")) == 2)

    bad = mk("bad", [[0, 0.5, 0.25, 80, 100]], [[]])
    bad["bpm"] = 100.0
    try:
        transplant_dicts(bad, dst, ["Strings"], 0.0, 1.0, verbose=False)
        rep("bpm 不一致必须报错", False, "居然没报错")
    except SystemExit as e:
        rep("bpm 不一致必须报错", "bpm 不一致" in str(e), str(e)[:40] + "…")

    try:
        transplant_dicts(mk("e", [], []), dst, ["Strings"], 0.0, 1.0, verbose=False)
        rep("窗内无可搬内容必须报错", False, "居然没报错")
    except SystemExit as e:
        rep("窗内无可搬内容必须报错", "没有任何可搬" in str(e), str(e)[:30] + "…")

    print("transplant_window 自检 %s" % ("PASS" if ok else "FAIL"))
    return ok


# ── CLI ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="把参照版某一窗移植到目标曲（分段，不动窗外）")
    ap.add_argument("ref", nargs="?", help="参照曲（曲名或 song.json 路径）")
    ap.add_argument("dst", nargs="?", help="目标曲")
    ap.add_argument("--at", default=None, help="时间窗（秒），如 19.0-19.6")
    ap.add_argument("--tracks", default="Strings", help="要搬的轨（逗号分隔，默认 Strings）")
    ap.add_argument("--name", default=None, help="新曲目名（默认 <目标>_tx）")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--ab-dir", default=None, help="A/B 片段目录（默认 <目标目录>/_tx_ab）")
    ap.add_argument("--audio", default=None, help="原曲音频（可选，用于 A/B 的『原曲』那一段）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return 0 if selftest() else 1
    if not (a.ref and a.dst and a.at):
        ap.error("要 <参照曲> <目标曲> --at t0-t1（或用 --selftest）")

    t0, t1 = [float(x) for x in a.at.replace("–", "-").replace("~", "-").split("-")]
    tracks = [x.strip() for x in a.tracks.split(",") if x.strip()]

    ref_p, _ = _resolve(a.ref)
    dst_p, dst_dir = _resolve(a.dst)
    with open(ref_p, encoding="utf-8") as fh:
        ref = json.load(fh)
    with open(dst_p, encoding="utf-8") as fh:
        dst = json.load(fh)
    print("参照版 %s（bpm %s）· 目标 %s（bpm %s）· 窗 %.2f–%.2fs · 轨 %s"
          % (os.path.basename(os.path.dirname(ref_p)), ref.get("bpm"),
             os.path.basename(dst_dir), dst.get("bpm"), t0, t1, ",".join(tracks)))

    out, _rep = transplant_dicts(ref, dst, tracks, t0, t1)

    name = a.name or (os.path.basename(dst_dir).rstrip("\\/") + "_tx")
    new_dir = os.path.join(SONGS, name)
    if os.path.isdir(new_dir):
        old = os.path.join(ROOT, "_archive_tx", name)
        os.makedirs(os.path.dirname(old), exist_ok=True)
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        shutil.move(new_dir, old)
        print("（旧同名曲目移到 %s）" % old)
    shutil.copytree(dst_dir, new_dir)
    sj = os.path.join(new_dir, "song.json")
    shutil.copy2(sj, sj + ".before_tx.bak")
    before = sha16(sj)
    out["name"] = name
    tmp = sj + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    os.replace(tmp, sj)
    print("写出 %s（sha %s → %s；备份 song.json.before_tx.bak）" % (sj, before, sha16(sj)))

    for fn in ("%s.mid" % os.path.basename(dst_dir), "%s_sf.ogg" % os.path.basename(dst_dir),
               "%s_sf.wav" % os.path.basename(dst_dir), "render.json"):
        p = os.path.join(new_dir, fn)
        if os.path.exists(p):
            os.remove(p)

    if not a.no_render:
        r = subprocess.run([PY, os.path.join(HERE, "make_song.py"), name, "--no-tune"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("[make_song] rc=%d" % r.returncode)
        if r.returncode:
            print((r.stderr or "")[-1200:])
            return 1

    ab = a.ab_dir or os.path.join(os.path.dirname(new_dir), "_tx_ab")
    os.makedirs(ab, exist_ok=True)
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        pad = max(0.0, (t1 - t0) * 0.6)
        ss, dur = max(0.0, t0 - 1.4 - pad), (t1 - t0) + 2.8 + 2 * pad
        clips = [(a.audio, "0_原曲.ogg"),
                 (os.path.join(os.path.dirname(ref_p), "%s_sf.wav"
                               % os.path.basename(os.path.dirname(ref_p))), "A_参照版.ogg"),
                 (os.path.join(dst_dir, "%s_sf.wav" % os.path.basename(dst_dir)), "B_移植前.ogg"),
                 (os.path.join(new_dir, "%s_sf.wav" % name), "C_移植后.ogg")]
        for src, fn in clips:
            if src and os.path.exists(src):
                subprocess.run([ff, "-y", "-i", src, "-ss", "%.3f" % ss, "-t", "%.3f" % dur,
                                "-c:a", "libvorbis", "-q:a", "7", os.path.join(ab, fn)],
                               capture_output=True, text=True)
                print("clip -> %s" % fn)
        print("A/B 片段目录：%s" % ab)
    except Exception as e:                                     # noqa: BLE001
        print("（A/B 片段跳过：%s）" % e)

    print("\n完成：songs\\%s\\%s.%s / %s_sf.ogg" % (name, name, "mid", name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
