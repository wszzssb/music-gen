# bgm-studio 技能：什么时候会被加载

> 本目录是一个 **DSH 技能**（`~/.dsh/skills/bgm-studio` 是指向这里的符号链接）。
> 技能正文在 `SKILL.md`；**这个文件只回答一件事：什么话会让它被加载进来。**

## 触发词（命中任一 → 应当加载本技能）

**中文**：音乐 · 歌 · 曲子 · 写歌 · 作曲 · 来一首 · 做一个 · BGM · 配乐 · 主题曲 · 插入曲 ·
音轨 · 伴奏 · 扒谱 · 扒和弦 · 扒成 MIDI · 音频转 MIDI · 转录 · 复刻某曲 · 还原某曲 ·
照着某首做 · 仿照某曲 · 渲染 MIDI / MIDI 渲染成音频（ogg/wav）· 音色对齐 ·
太电音 · 有杂音 · 太闷 · 不够宽 · 不够欢快 · 不像原曲

**英文**：song · music · bgm · midi · ost · transcribe · arrange · cover

⚠ **最泛的上位词（音乐 / 歌 / 曲子 / song / music）必须排在最前面。**
实测教训：第一版词表是按"专业说法"拼的（写歌 / 作曲 / BGM / 扒谱…），
用户说"复刻**音乐**的midi"就没被认出来 —— 因为技能加载**没有语义兜底，纯靠词面覆盖**。

## 加载机制（读源码得到，不是猜）

- **发现**：`@deepseek-ai/dsh-skill-filesystem` 扫
  `~/.dsh/skills`、`~/.agents/skills`、项目级 `.dsh/skills`、`.agents/skills`。
- **没有任何"关键词自动匹配"的代码**（grep `trigger`/`keyword`/`autoLoad` 零命中）。
  本技能的 `description` 只是被列进系统提示的**技能目录**，
  **加载与否 100% 由模型按 description 判断**。`@deepseek-ai/dsh-tool-skill` 的提示词：

  > If the user names a listed skill, **or the task clearly matches its description**,
  > call the `skill` tool with the exact name before acting.

- 结论：**词表宁全勿缺**；用户新出现的说法就照抄进表。

## 两层词表必须同步

| 层 | 文件 | 生效时机 |
|---|---|---|
| 技能目录（`description`） | `SKILL.md` 的 frontmatter | **当场**（实测：改完立即收到 "available skill catalog changed"） |
| 常驻兜底（触发词表） | `~/.dsh/AGENTS.md` 的"音乐任务"段 | 每次对话加载 |

⚠ `frontmatter` 丢了 `name` / `description`，技能会**直接从技能目录里消失**
（自检 `t_docs_budget_and_skill_intact` 在守这一条）。

## 排错

在任意对话里看系统提示的 **available_skills 列表**里有没有 `bgm-studio`：

- **不在列表** → 发现层问题（符号链接 / frontmatter / 路径）：
  `ls -la ~/.dsh/skills/` + 检查 `SKILL.md` 开头的 `---` 块；
- **在列表但没被调用** → "没被认出来"，补触发词（见上）。

完整机制与备份 → `~/.dsh/docs/SKILL-LOADING.md`（副本在 `docs/HOST-DOCS/`）。
