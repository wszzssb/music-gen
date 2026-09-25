#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""arrange_sections.py —— **把"全曲一套"的编配改成"按段证据"**（siren_end2 12 轮验证过的判据）

一次只改一个维度（技能 §18）；四个开关各自独立、默认全关，**要给证据文件才动**：

| 开关 | 写什么 | 判据（证据文件） | 实测（siren_end2） |
|---|---|---|---|
| `--mix on` | `sections[i].arr.mix = {轨: CC7}` | `report_sections --json` 的**逐段 RMS 差**；死区 **1.5dB**、上限 **±3dB** | 平均\|RMS差\| 2.58 → **2.29** |
| `--vel on` | `sections[i].arr.vel = 乘数` | **整段** 2–6kHz 占比 我方÷原曲 > **1.5** → `1/√r`（下限 0.80，**只降不升**） | 只命中 2 段，指标几乎不动 |
| `--prog 轨=程序号 --from-sec S17` | `arr.prog = {轨: 程序号}` | **用户真值锚点**（"1 分 50 之后是小提琴"）；引擎已支持任意轨 | ⚠ GM40 在**多音和弦线**上是**负结果**（S17–S22 的 2–6k 3.4–5.4% → 10.4–29.9%） |
| `--shift 轨=半音 --from-sec S17` | `arr.shift = {轨: 半音}` | 同上，**音色必须与音区一起改**才成立 | 本曲 `Strings` 的全局移调本来就是 0 → no-op |

## 三条踩过的坑（都写在这里，别再犯）
1. **CC7 是线性增益（0–127），不是 dB**：`base + 3` 在 base=70 时只有 **+0.35dB**。
   正确换算 `新 = base × 10^(dB/20)`（+3dB @70 → **96**）。
2. **原始 `song.json` 里没有 `mix` 键** —— 音量表是引擎 `load()` 时从风格预设补的；
   直接读文件会得到空表、方案"改了 0 段"。**一律用 `song_engine.load()` 拿有效配置**。
3. **`render_midi.py` 的母带链会整曲归一化** → 逐段 CC7 在这个链下**是零和的**
   （抬高大部分段 = 把没抬的段相对压下去，实测 S01 −0.8 → −4.9dB）。它只能"压平缺口"。

## 用法

```bash
python scripts\arrange_sections.py --song-json songs\<曲>\song.json --table <编制表.json> \
       --sec-json <report_sections.json> --bright-json <亮度表.json> [--mix on] [--vel on] \
       [--prog Strings=40 --from-sec S17] [--apply|--dry]
python scripts\arrange_sections.py --selftest
```
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                                # noqa: E402
import song_engine as se                      # noqa: E402

DEAD_DB, CAP_DB = 1.5, 3.0
TRIG, VEL_LO = 1.5, 0.80


def cc7_for(base, delta_db):
    """**纯函数**：把"想改多少 dB"换成 CC7 值（CC7 是线性增益，不是 dB）。"""
    return max(0, min(127, int(round(base * (10.0 ** (delta_db / 20.0))))))


def vel_for(ratio):
    """**纯函数**：亮度比 r = 我方 2–6k ÷ 原曲 → 力度乘数（只降不升，下限 VEL_LO）。"""
    if ratio is None or ratio <= 1.0:
        return None
    if ratio <= TRIG:
        return None
    return round(max(VEL_LO, min(1.0, 1.0 / (ratio ** 0.5))), 3)


def selftest(verbose=True):
    ok = []
    ok.append(("+3dB@70→99", abs(cc7_for(70, 3.0) - 99) <= 1))
    ok.append(("0dB 不变", cc7_for(70, 0.0) == 70))
    ok.append(("-3dB@70→50", abs(cc7_for(70, -3.0) - 50) <= 1))
    ok.append(("夹上界", cc7_for(120, 6.0) == 127))
    ok.append(("r=1.2 不动", vel_for(1.2) is None))
    ok.append(("r=2.25→0.667↑夹到 0.80", vel_for(2.25) == 0.80))
    ok.append(("r=4→0.5↑夹到 0.80", vel_for(4.0) == 0.80))
    if verbose:
        for nm, v in ok:
            print("  %-22s %s" % (nm, "PASS" if v else "FAIL"))
        print("arrange_sections 自检 " + ("PASS" if all(v for _n, v in ok) else "FAIL"))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser(description="段级编配（arr.mix / arr.vel / arr.prog / arr.shift）")
    ap.add_argument("--song-json")
    ap.add_argument("--table")
    ap.add_argument("--sec-json", help="report_sections --json 产物（--mix 用）")
    ap.add_argument("--bright-json", help="逐段亮度表 {段: [我方, 原曲]}（--vel 用）")
    ap.add_argument("--mix", default="off", choices=("on", "off"))
    ap.add_argument("--vel", default="off", choices=("on", "off"))
    ap.add_argument("--prog", default=None, help='段级音色，如 "Strings=40"')
    ap.add_argument("--shift", default=None, help='段级移调，如 "Strings=0"')
    ap.add_argument("--from-sec", default=None, help="从哪一段起生效（prog/shift）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not a.song_json:
        ap.print_help(); return 1

    d = se.load(a.song_json)                  # ⚠ 必须用引擎的有效配置（见坑 2）
    secs = d["sections"]
    names = [s["name"] for s in secs]
    B = float(d.get("bar_beats") or 4.0)
    b0, bounds = 0, {}
    for s in secs:
        nb = int(s.get("bars") or 4)
        bounds[s["name"]] = (b0, b0 + nb)
        b0 += nb
    ne = d.get("notes_extra") or {}
    n_chg = 0
    # 编制表（可选）：把"原曲这一段是什么在响"打在每一行旁边 —— 让人一眼看出
    # "这一段的改动有没有编制依据"，而不是只看到一堆 dB 数字。
    tbl = {}
    if a.table and os.path.exists(a.table):
        for r in (json.load(open(a.table, encoding="utf-8")).get("sections") or []):
            att = r.get("attack_ms")
            tbl[r.get("name")] = "%s/%s" % (r.get("lead") or "-",
                                            "-" if att is None else "%.0fms" % att)

    def _ctx(nm):
        return ("%s" % tbl[nm]) if nm in tbl else "-"

    if a.mix == "on":
        rows = {r["name"]: r for r in json.load(open(a.sec_json, encoding="utf-8"))["rows"]}
        base = {k: int(v[1]) for k, v in (d.get("mix") or {}).items()}
        print("== arr.mix（段级 CC7）· 死区 %.1fdB · 上限 ±%.1fdB · CC7 按线性增益换算"
              % (DEAD_DB, CAP_DB))
        print("%-7s %-12s %9s %9s %8s | %s" % ("段", "原曲编制", "我方RMS", "原曲RMS", "差dB", "动作"))
        for s in secs:
            r = rows.get(s["name"])
            if not r:
                continue
            diff = float(r["rms_mine"]) - float(r["rms_ref"])
            act = "—"
            if abs(diff) > DEAD_DB:
                delta = max(-CAP_DB, min(CAP_DB, -diff))
                x, y = bounds[s["name"]]
                tracks = [k for k in base if any(
                    x <= int(it[0]) < y for it in
                    ((ne.get(k) or {}).get("notes") if isinstance(ne.get(k), dict) else (ne.get(k) or [])))]
                if tracks:
                    s.setdefault("arr", {})["mix"] = {k: cc7_for(base[k], delta) for k in tracks}
                    act = "%+.1fdB → %s" % (delta, " ".join("%s:%d→%d" % (k, base[k], cc7_for(base[k], delta))
                                                            for k in tracks))
                    n_chg += 1
            print("%-7s %-12s %9.1f %9.1f %+8.1f | %s"
                  % (s["name"], _ctx(s["name"]), r["rms_mine"], r["rms_ref"], diff, act))

    if a.vel == "on":
        b = json.load(open(a.bright_json, encoding="utf-8"))
        print("== arr.vel（段级力度乘数）· 触发 >%.2f · 下限 %.2f（只降不升）" % (TRIG, VEL_LO))
        print("%-7s %-12s %9s %9s %7s | %s" % ("段", "原曲编制", "我方%", "原曲%", "比值", "动作"))
        for s in secs:
            v = b.get(s["name"])
            if not v:
                continue
            mine, ref = v
            r = (mine / ref) if ref and ref > 0 else None
            vel = vel_for(r)
            if vel is not None:
                s.setdefault("arr", {})["vel"] = vel
                n_chg += 1
            print("%-7s %-12s %9.2f %9.2f %7s | %s"
                  % (s["name"], _ctx(s["name"]), mine, ref, ("%.2f" % r) if r else "-",
                     ("vel=%.3f" % vel) if vel is not None else "—"))

    for flag, key, val in ((a.prog, "prog", a.prog), (a.shift, "shift", a.shift)):
        if not flag:
            continue
        track, num = flag.split("=")
        track, num = track.strip(), int(num)
        print("== arr.%s：从 %s 起 %s → %d" % (key, a.from_sec or names[0], track, num))
        started = a.from_sec is None
        for s in secs:
            if s["name"] == a.from_sec:
                started = True
            if not started:
                continue
            x, y = bounds[s["name"]]
            cnt = sum(1 for it in ((ne.get(track) or {}).get("notes")
                                   if isinstance(ne.get(track), dict) else (ne.get(track) or []))
                      if x <= int(it[0]) < y)
            if key == "prog" and not cnt:
                print("   %-7s 该轨无音，跳过" % s["name"])
                continue
            s.setdefault("arr", {}).setdefault(key, {})[track] = num
            n_chg += 1
            print("   %-7s %s=%d（该轨 %d 音）" % (s["name"], track, num, cnt))

    print("合计改动 %d 处" % n_chg)
    if a.dry or not a.apply:
        print("（--dry / 未加 --apply：没写文件）")
        return 0
    shutil.copy2(a.song_json, a.song_json + ".pre_arrsec.bak")
    json_io.save(a.song_json, d)
    print("已写回 %s（备份 .pre_arrsec.bak）" % a.song_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
