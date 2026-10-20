# LLM 接入音乐的路径（优先级）

> 用户 2026-10-20 定的接入顺序。**先看这里，再选工具。**

## 第一选择：LLM 写大纲（自由创作，**必须符合主题理论**）→ 本机引擎按大纲生成

**适用**：有文本 LLM（或对话里就是 LLM）参与时。

⚠ **合规口径（2026-10-20 用户定）**：**不要求“必须从 ≥8 首主题模板聚合”**。
LLM 可以自由创作大纲/spec，但**必须符合主题的理论**：
- 调性/和声走向符合 `refs/themes/<主题>.json` 的 `harmony` 画像；
- 节奏型/配器符合该主题的 `rhythm` / `arrangement`；
- spec 里写 `theme: <主题名>`，据此留痕（`basis` 指主题画像即可，不必逐首模板）。

1. **LLM 写大纲**（自由创作，但按主题理论约束）：
   - 选主题（`theme_pack.py --list-themes`，画像在 `refs/themes/<主题>.json`）；
   - 段落 / 小节数 / 和弦 / 编配自由规划，但**调性/和声/节奏/配器不得偏离主题画像**；
   - BPM 在主题实测区间内。
2. **本机引擎按大纲生成**（三选一）：
   - `scripts/build_song.py <spec.json>`（spec 带 `theme`）；
   - `scripts/imitate_plan.py <曲目> --plan <结构表.json>`（仿写，需参考曲）；
   - `scripts/expand_sections.py`（主题包骨架扩成大曲式）。
3. 渲染：`make_song.py <曲目>` 或 `render_midi.py`。

**为什么大纲不进 MIDI-LLM 的 prompt**：MIDI-LLM 对自然语言的控制力实测很弱（“solo violin”出小提琴+中提琴+大提琴三重奏；“piano piece”出鼓和小号），大纲写进 prompt 只能部分生效。大纲落进 `song.json`/spec 才是硬约束。

## 没有 LLM 时：MIDI-LLM 片段 → 本机引擎补全

**适用**：没有文本 LLM 参与，只有 MIDI-LLM + 本机引擎。

1. **MIDI-LLM 生成默认 2046 tokens，最高 3250 tokens（硬限制）**。
   - 2046 ≈ 35 秒（训练长度 2048 的安全值）；3250 ≈ 55 秒（适度外推，留足 time token 余量）。
   - **超过 3250 拒绝**（再往上质量/稳定性无保证）；4096（92.5 秒）只作实验、不接常规流程。
   - AMT 的 100 秒（time 0–10000）是 tokenization 上限，与训练长度 2048 是两回事。
2. **本机引擎补全**：
   - 扩段：`scripts/segment_edit.py <曲目> --target S01=16,...`
   - 补编配：旋律进 `melody`、引擎按和弦生成伴奏（`song.json` 的 `arr` 开 piano/strings/bass/perc）
   - 渲染：`render_midi.py`
3. 交付。

## 两条路的边界

| | 有 LLM | 无 LLM |
|---|---|---|
| 结构/和弦 | LLM 大纲（硬约束） | `midi_to_song` 自动识别 / 主题包 |
| 音符来源 | 引擎生成 / 参考曲 | MIDI-LLM 片段 |
| 长度 | 大纲定 | MIDI-LLM 35–100 秒 + `segment_edit` 扩段 |
| 控制力 | 高（song.json 可改） | 中（受 MIDI-LLM prompt 弱控制） |
