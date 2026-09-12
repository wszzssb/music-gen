# 17 b73_slow_evening —— 仿 参考曲.flac

**任务**：不用任何旧画像从零重扒 → 生成"听起来像"的音乐 → 并按用户要求做**分段拟合**与**修复误判/不贴合**。

| 项目 | 值 |
|---|---|
| 调性·速度 | **F 小调，72.8 BPM**（小节 3.297s） |
| 长度 | 72 小节 ≈ 240.6 秒 |
| 结构 | Intro4 + A8 + B8 + C1 8 + A2 8 + B2 8 + C2 8 + Bridge8 + C3 8 + Outro4 |
| 编配 | ballad；钢弦吉他(尼龙备用) + 钢琴 + 弦乐 + pad + 稀疏沙锤；**钟琴只在副歌开**（分段拟合） |
| 分段曲线 | 段落级 CC7 自动化：Intro −8 / A 0 / B −6 / C +4 / Bridge −10 / Outro −6 |

## 最终实测（vs 参考画像 hitorigohan2）

| 指标 | 本曲 | 参考 | 差 |
|---|---|---|---|
| 速度 | 72.8（回测 73.0） | 72.8 | **≈0** |
| **立体声宽度** | **0.793** | **0.793** | **0.000** |
| 频谱质心 | 858 Hz | 945 | −87 |
| 315–630Hz | 0.0 | 0.0 | **0.0** |
| 630–1250Hz | −2.5 | −4.1 | +1.6 |
| 160–315Hz | −6.8 | −7.5 | **+0.7** |
| 2500–5000Hz | −24.8 | −27.5 | **+2.7** |
| 5000–10000Hz | −41.7 | −47.3 | **+5.6** |
| 10000–18000Hz | −57.2 | −58.8 | **+1.6** |
| 20–40Hz | −46.5 | −45.5 | −1.0 |
| **段间起伏 5–10kHz** | **14.3dB** | **9.0~13.1dB** | 同量级 ✔ |

**"清脆"是段间对比，不是整体更亮** —— 这是这轮最大的收获：参考曲的暗段低到 −5.4dB、
亮段 +7.7dB（差 13dB），而第一版成品全曲一条直线（起伏 4.5dB），所以"亮但闷"。
现在用**段落级 CC7 自动化**把起伏做到 14.3dB，5–10k 只差 5.6dB。

## 已修的误判 / 不贴合

| 问题 | 处理 |
|---|---|
| 速度八度层（145.6 vs 72.8） | 用**起音密度**定层（145.6 层只有 0.94 音/拍），`profile_ref --bpm 72.8` 钉死 |
| 人声分类器把独奏吉他误判 `vocal_forward` | Demucs 判 vocals/bass/drums **全静音**（真·器乐）→ 记入坑 89 |
| 旋律 4 处八度错位大跳 | 重写 B 段，现**零处 >7 半音大跳** |
| 和弦与旋律对不上（8 处） | 逐条核对；Bridge 单独一份 `b_bridge` 旋律（与 B 段共用小节但和弦不同） |
| 段间无起伏（听感闷） | 分段拟合（本轮新增工具链） |
| `check_song --fix` 改坏和弦 | 改为只读它的**诊断**、手工改排列 |

## 复现

```powershell
$py = "D:\software\skill\.venv\Scripts\python.exe"
cd D:\software\skill
& $py scripts\check_audio.py <参考曲> --deep                       # ① 体检（含速度层建议）
& $py scripts\profile_ref.py <参考曲> hitorigohan2 --bpm 72.8       # ② 整曲画像（钉死层）
& $py scripts\analyze_sections.py <参考曲> hitorigohan2             # ③ **分段目标**（新）
& $py scripts\new_song.py 17_x --from <模板> --ref hitorigohan2 \
      --from-sections hitorigohan2                                  # ④ 自动填分段曲线（新）
& $py scripts\check_song.py 17_x                                   # ⑤ 渲染前校验（含编配干跑）
& $py scripts\make_song.py 17_x                                    # ⑥ 渲染+调参+成绩单
```

## 文件

- **`b73_slow_evening_sf.ogg`**（240.6s/6.3MB）· `b73_slow_evening_sf.wav` · `b73_slow_evening.mid`
- `song.json`（含分段 `arr.mix` 曲线）· `render.json`（含 `align_exempt`）
