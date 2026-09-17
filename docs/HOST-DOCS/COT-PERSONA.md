<!-- ⚠ 这是**备份**，不是生效副本：真正被 DSH 加载的是宿主上的
     `C:/Users/z/.dsh/docs/COT-PERSONA.md`。要改请改那份（改完新会话生效），再重跑 `sync_host_docs.py` 同步过来。 -->

# 思维链语言：改哪个文件（AGENTS.md 的展开版）

> AGENTS.md 里只留指针，完整内容在这里。**只在"要改思维链语言"时才需要读这个文件。**
> 下面每一条都**逐文件核对过**（2026-09-17），不是转述。

思维链语言 = system 层 persona，**每条路由 / 每个模式各有自己的 persona**。

| 模式 | 改哪里 |
|---|---|
| minimal（默认）· standard · code · cordis | `<dsh 安装目录>/config/agent-presets/<模式>/agent.cordis.yml` —— 找 **`- id: persona`** 条目，改它的 **`config.text`** |
| router-spec · router-standard | `~/.dsh/.agent-presets/<模式>/router-core.mjs` —— `personaFor()` = `withChineseCoT(rawPersonaFor(mode, modelId))` |

`<dsh 安装目录>` = `C:\Users\z\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh`

## 实测到的结构（照这个改，别猜字段名）

四个 preset 目录都在：`code` · `cordis` · `minimal` · `standard`，各含一个 `agent.cordis.yml`。
persona 条目长这样：

```yaml
- id: persona
  name: '@deepseek-ai/dsh-persona'
  config:
    text: >-                 # standard/code/cordis 用折叠标量
      You are a coding agent powered by the {{model}} model. …
      Reason internally in Chinese only — every thinking/reasoning block …
```

⚠ **`minimal` 不一样，改之前先看清**：

- 它用 `text: |-`（保留换行），**并且带 `complete: true` + `includeRuntimeContext: false`**；
- 文件头注释写明 *"The persona is the complete system prompt"* ——
  **其它条目（identity / tool guidance / 组装期监听器）不能再往里加提示词**，
  给 minimal 加内容**只能改这一段 `text`**；
- 它的 persona 目前就是两行：自我介绍 + 中文思维链要求。

## router 侧（`router-core.mjs`）

```js
const ZH_COT = '\nReason internally in Chinese only — …'
function withChineseCoT(persona) { return persona + ZH_COT }
export function personaFor(mode, modelId) {
  return withChineseCoT(rawPersonaFor(mode, modelId))
}
```

是**追加**（不是替换），且**幂等**（persona 是常量、不原地重包装）——
所以 router 的每种模式都自动带上中文要求，**新增模式也不会漏**。

## 生效机制（2026-09-17 读了实现，不是转述）

`@deepseek-ai/dsh-agent-presets/lib/index.js`：

```js
async function compositionStamp(path) {          // 取"文件戳"
  try { const { mtimeMs, size } = await stat(path); return { mtimeMs, size }; }
  catch { return; }
}
function sameStamp(a, b) {                       // 戳一样 = 没变
  return a.mtimeMs === b.mtimeMs && a.size === b.size;
}
```

`lib/types/index.d.ts` 把整条规则写明了（原文）：

> a settled success serves until the composition FILE visibly changes — each generation
> records its file stamp, and a stale stamp starts the next generation **for sessions
> created afterwards**. Sessions already joined keep the generation they run on; a
> superseded one is never disposed while the process lives …

**结论（照这个做）**：

- 检测依据 = **mtime + size**；**无需重启进程** ✓
- **只在"之后新建的会话"上生效** —— 当前会话里改完**不会**热切换
  （这正是"改完要开新会话"的原因，不是玄学）；
- 挂载失败会被移除 → **文件修好后，下个会话会重试**（不会一直坏着）；
- 被取代的那一代在进程存活期间**不销毁**（占内存、无害）。

## 其它两条要点

- **随包 preset 会被 npm 升级覆盖** → 升级后要重加那句 `Reason internally in Chinese only …`；
  新增 preset 也记得加。
- 工作区指令（AGENTS.md）**压不动思维链**：实测加指令后中文占比 ~58%（此前多数 <10%），
  属于"强引导、非保证"；真正的强约束在 persona 层。
