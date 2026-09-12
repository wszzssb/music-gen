# 01 Summer Seaside —— 夏日海边风格

| 项目 | 值 |
|---|---|
| 风格 | 夏日海边 / 抒情流行，仿 `BGM16c.ogg` 的**和声语言** |
| 调性·速度 | **A 大调，150 BPM，4/4**（参考曲是 F 大调，高一个半音便于吉他按弦） |
| 长度 | 40 小节 ≈ 64 秒（+ 混响尾 ≈ 69 秒） |
| 结构 | Intro 4 + A 8 + B 8 + A' 8 + B' 8 + Outro 4 |
| 和声 | A–C#m7–D–Dm7–A/E–F#m7–Bm7–E7；副歌 Bm7–C#7–Dm7–E7–**A7**–F#m7–Bm7–E7；尾奏用**三全音代理 A#7 → A6/9** |
| 编配 | 7 轨：主旋律(钢琴) / 钢弦吉他分解 / 钢琴反拍 / 弦乐 / 原声贝斯 / 钟琴 / 鼓组(沙锤+轻鼓) |

## 复现

```powershell
$py = "D:\software\skill\.venv\Scripts\python.exe"
cd D:\software\skill
& $py songs\01_ac150_seaside\ac150_seaside.py            # 作曲 → MIDI + 合成器试听版
& $py scripts\render_midi.py songs\01_ac150_seaside\ac150_seaside.mid ac150_seaside_sf `
     --width 2.2 --rms -16.9 --shelf 3.0                    # 真音源成品
```

## 实测对比（vs `D:\refs\BGM16c.ogg`，同口径）

| 指标 | 本曲 | 参考 |
|---|---|---|
| 响度 RMS | −17.2 dBFS | −16.9 |
| 立体声宽度 | **0.51** | 0.51 |
| 频谱质心（主歌） | 2292–2395 Hz | 2364–2410 |
| 10–18kHz | −18.8 dB | −19.6 |
| 高频起音密度 | 5.4/s | 4.9/s |

**尾部无底噪**（−240dBFS 数字静音）、零削波、OGG 275kbps。
差异说明：前奏有意轻 2dB（段落起伏）；A'/尾奏比参考略暗约 1000Hz（参考曲后半段会加亮）。

## 文件

- `ac150_seaside.mid` —— **主交付**，7 轨 GM 音色 + CC10 声像/CC7 音量，可换任何更好的音源
- `ac150_seaside_sf.ogg` / `.wav` —— 真音源渲染成品（FluidSynth + GeneralUser GS）
- `ac150_seaside.ogg` / `.wav` —— 自写合成器试听版（音色是替代品，只用来确认旋律和声）
