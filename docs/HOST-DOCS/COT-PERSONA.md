<!-- ⚠ 这是**备份**，不是生效副本：真正被 DSH 加载的是宿主上的
     `C:/Users/z/.dsh/docs/COT-PERSONA.md`。要改请改那份（改完新会话生效），再重跑 `sync_host_docs.py` 同步过来。 -->

# 思维链语言：改哪个文件（AGENTS.md 的展开版）

> AGENTS.md 里只留指针，完整内容在这里。**只在"要改思维链语言"时才需要读这个文件。**

---

## 为什么要中文思维链 / 代价多大（2026-10-01 查证，外部证据）

用户口径：思维链要中文（**为了自己能看懂**）。下面三条是本轮查到的实测数字，
**都不是本机实测**（没测 deepseek-flash），引用时带上来源与适用范围。

| 问题 | 证据 | 数字 |
|---|---|---|
| **用中文思考 vs 英文，差多少** | [Tam et al. 2025（Language Matters）](https://arxiv.org/html/2505.17407v1)：中/英都是"推理 hub"语言；把思考语言**强制** prefilling 成目标语言后对比 | MATH-500：中文比英文低 **+1.9%**（4 模型平均）；MMLU：**+2.9%**。**模型间不一致**：DeepSeek-R1-Distill-Qwen-14B 上中文**反而高 1.4%**。对照低资源语言：斯瓦希里 +31.5%、泰卢固 +23.7% |
| **强制"只准一种语言"会怎样** | [Li et al. 2025（EMNLP，中英双语推理模型）](https://arxiv.org/abs/2507.15849)：DeepSeek-R1 上**抑制语言混用会掉准确率** | **强制单语解码 = MATH-500 掉 5.6 个百分点**；语言混用是**策略性**行为（用探针引导切换可 +2.92%） |
| **偏离模型偏好语言的代价** | [Qi et al. 2025（EMNLP Findings，XReasoning）](https://aclanthology.org/2025.findings-emnlp.1103/) | 让模型"用用户的语言思考"提升可读性/可监督性，但**降低答案准确率**；小模型损失更大（8B 平均 26.8%，QwQ-32B 5.4%） |

**读法（重要）**：
1. **"用中文思考"本身几乎不吃亏** —— 中文是 hub 语言，平均差 1.9%/2.9%，且模型间不一致；
2. **真正的风险是 persona 里那句"never English"/"一律中文"** —— 它等于强制单语，
   而实测强制单语在中英双语模型上掉 **5.6 分**（MATH-500）；**中文为主 + 关键处混用英文**才是有据的做法；
3. 任务类型相关：**安全/文化类**用母语思考通常更好（中文思考时毒性 ASR 7.4% vs 英文 7.7%）；
4. 轻量模型偏大端（8B 蒸馏：中文 vs 英文 +5.2%），大模型 1~2% ⇒ 对 flash 档要按大端预期。

**若要把风险压到最低**（改 persona 一处即可，措辞建议）：
> 中文为主；**数学/代码/公式/术语与关键推理步骤允许直接用英文**（可读性由中文承担，
> 精度由"不强制单语"承担）。

⚠ **本机未实测**：以上都是别人的模型与实验设置（prefill 控制解码），不是 deepseek-flash +
persona 指令的直接测量。要自己的数字得做 A/B（改机器级 patch → 各开新会话跑同一套题），
成本与噪声都不小（每个会话单样本）。

---

## ⚠ 2026-10-01 复核：**下面那套路径全部失效，而且当前那句中文要求没生效**

**本机现在跑的是桌面版**（`D:\software\dsh\DeepSeek Harness.exe`，Electron，`app-update.yml` →
`channel: nightly`）；npm 全局那份 `@deepseek-ai/dsh`（0.1.6-alpha.2，终端 `dsh --version` 指向它）
**还在、但不是本会话在跑的那份**——两份并存是这套文档过时的根因。

| 旧文档说的 | 2026-10-01 实测 |
|---|---|
| `<dsh 安装目录>/config/agent-presets/<模式>/agent.cordis.yml` | **不存在**：npm 那份没有 `config/`；桌面版在 `resources/app.asar`（121MB 的 ASAR 包）里 |
| `~/.dsh/.agent-presets/<模式>/router-core.mjs` | 该目录**空**；asar 原文：*"a user preset was a directory `$DSH_HOME/.agent-presets/<id>/` … **Nothing reads that directory any more**"* |
| 四个 preset：code / cordis / minimal / standard | 随包 preset id 现在是 **`standard` · `ptc` · `minimal` · `cordis`**，各是 `@deepseek-ai/dsh-web-app` bundle 里的 `presets/<id>.patch.yml` |

**新机制（asar 原文 + 实测）**：
- preset 是 **声明行**：行 id 约定 `preset-<id>`，里面的 persona 行 id 仍是 **`persona`**；
- *"Installed, the bundle resolves from the **dsh installation, not the profile**"* ⇒ 随包预设
  **用户侧改不了**（在安装目录的 asar 里）；
- 会话记录里能直接看到用的是哪个：`~/.dsh/sessions/<项目>/<会话>/session.v4.jsonl.zstd` 的
  `{"type":"session", … "agentPreset":"standard"}`；
- 机器级 patch `~/.dsh/cordis.patch.yml` **仍会被加载**（asar：`loadOptionalPatches(binName,
  join(context.home, "cordis.patch.yml"))`），但优先级是
  **命令行 `--patch` > 机器级 patch > profile 的 cordis.patch.yml**。

**当前那句中文要求为什么没生效**：它挂在 `- id: system-prompt` 的 `config.persona` 上，
而 **preset 自己的 `persona` 行覆盖了它**（那份 patch 的注释当年就警告过这件事，并说
"preset 里也已各自加过同一句" —— 但 preset 现在在 asar 里，那处修改随打包丢了）。

**证据（可复跑）**：本会话（`agentPreset: standard`）的 `system/message` 记录 **6231 字符**，
里面**没有** `Reason internally in Chinese`；`grep -a -c "Reason internally in Chinese"
D:\software\dsh\resources\app.asar` = **0**（随包预设里也没有那句）。

复跑命令（Python 3.14 自带 zstd，别装第三方包；⚠ 路径用 `C:/...`，MSYS 的 `/c/...` 喂给
Windows python 会 `FileNotFoundError`）：

```python
# 读"实际发给模型的 system 段"（只看 system/message 记录，别整文件搜 —— 会搜到工具回显）
from compression import zstd as z; import glob, json, os
p = sorted(glob.glob(r'C:\Users\z\.dsh\sessions\**\session.v4.jsonl.zstd', recursive=True),
           key=os.path.getmtime)[-1]
raw = z.decompress(open(p, 'rb').read()).decode('utf-8', 'replace')
for ln in raw.split('\n'):
    if '"system/message"' in ln:
        print('含那句中文要求:', 'Reason internally in Chinese' in ln, '· 长度', len(ln))
```

**要修的话**（2026-10-01 已做到最后一步：**只剩安装**）：

| 层 | 能改到"挂了 preset 的会话"吗 | 实测 |
|---|---|---|
| `~/.dsh/cordis.patch.yml`（机器级） | ❌ | `patch: entry "preset-standard" not found` |
| `~/.dsh/profiles/web/cordis.patch.yml`（profile 级） | ❌ | 同上 |
| 命令行 `--patch <file>` | ❌ | 同上 |
| **安装一个 bundle**（官方唯一路径） | ✅（待装） | 官方 `editing-cordis-compositions` SKILL：改随包 preset = 安装一个"patch 里覆盖该行"的 bundle（`plugin_manager` `install_bundle`） |

**为什么三层都不行**：preset 声明是**更晚**才插进 Loader 树的（`--dump-config` 里**根本没有**
`preset-standard` 这一行）。而 `@deepseek-ai/dsh-persona` 的 README 明写：preset 里的人设段
**遮蔽**部署级默认值（"两者分别替换对应的部署默认值，**而不是出现在其旁边**"）
⇒ 挂 `standard` 的会话里 `system-prompt.personaPrefix` 被顶掉，改它没用。

**2026-10-01 已落地 + 已验证的方法**（别再用"覆盖 preset 行"那套）：

1. `~/.dsh/cordis.patch.yml` 已重写：键名改回 **`personaPrefix` / `personaSuffix`**
   （旧版用的 `persona` **不是有效键** —— 实测把两个正确键一起弄没了），文案换成新口径。
   作用域：**没挂 preset 的会话**（headless 等）。
   复核：`dsh --profile web --dump-config | grep -A8 'id: system-prompt'`；
   旧文件备份在 `~/.dsh/_backup/cordis-patch-<时间戳>/cordis.patch.yml`。

2. **bundle 已备好（插入新 preset，不是覆盖）**：`C:\Users\z\.dsh\local-bundles\dsh-cot-zh\`
   - 它 **insert 一个新 preset**：`id: zh` · 展示名「中文思维链（standard + 混用英文）」·
     plugins 与随包 `standard` **逐条一致**（33 个 `- id:` 核对过），只把 persona 的 `prefix`
     换成新口径。
   - **为什么不用"按 id 覆盖 preset-standard"**（实测）：覆盖要命中那一行，而 preset 行是
     **运行时**才插进 Loader 树的 ⇒ `~/.dsh/cordis.patch.yml`、`profiles/<p>/cordis.patch.yml`、
     命令行 `--patch` **三层都报 `entry "preset-standard" not found`**；用 CLI 装成 bundle 后
     **冷启动合成时仍报 not found**。**插入新行不依赖已存在的行** ⇒ 一次成功。
   - **端到端验证（在 CLI 能管的 web profile 上做的）**：`dsh plugin --profile web add <bundle目录>`
     → `dsh --profile web --dump-config` 出现 `- id: preset-zh …`（persona 就是新文案）、**0 告警**；
     `dsh plugin --profile web remove @local/dsh-cot-zh` 也能干净撤回（都验过，现在已撤回）。

3. **装到 App 用的 `desktop` profile —— 只能从 App 内装**（CLI 明确拒绝：
   `error: profile "desktop" is managed exclusively by the Electron application`）。两条路：
   - **设置 → 插件 → 安装**，spec 填本地绝对路径
     `C:\Users\z\.dsh\local-bundles\dsh-cot-zh`（官方支持本地路径 spec，会先 inspect 读它的 package.json）；
   - 或 **Agent preset 页的 Creator（创建）入口**新开一个 `cordis` 会话（官方文档：*"Creator 入口
     开启一个使用 `cordis` preset 的新任务"*），在那里面让 agent 用 `plugin_manager`
     `action: install_bundle`、`target` 指到该目录（`cordis` preset 里该工具是启用的，
     `standard`/`ptc` 都是 `disabled: true`）。
   装完：**设置 → Agent preset** 会多一张卡，选它当默认（或每个新会话手动选）。
   ⚠ 只对**之后新建**的会话生效；`--dump-config` 看的是冷启动合成，最终判据是**新会话的
   `system/message`**（读法见上面的脚本）。
   ⚠ 该 preset 的 plugins 是 2026-10-01 从随包 `standard` 复制的**快照**，**dsh 升级后要重新生成**
   （生成脚本：`D:\test\_tmp\global-audit\gen_insert_bundle.py`，读 asar 里的随包 preset 做文本变换）。

**口径（用户 2026-10-01 定）**：思维链**中文为主**（用户要能读），但**不强制单语** ——
依据：强制单语解码在中英双语推理模型上 MATH-500 掉 **5.6 个百分点**（[arXiv 2507.15849](https://arxiv.org/abs/2507.15849)）；
单纯"中文 vs 英文"平均只差 **1.9%（MATH-500）/ 2.9%（MMLU）**，且模型间不一致（[arXiv 2505.17407](https://arxiv.org/html/2505.17407v1)）。

⚠ **本机还没验证**：装完 bundle 之后，新会话的 system prompt 里到底有没有新文案
（要用上面的脚本读 `system/message`）。

---

## 历史资料（2026-09-17 核对，**路径已失效，只留作机制说明**）

思维链语言 = system 层 persona，**每条路由 / 每个模式各有自己的 persona**。

| 模式 | 当年改哪里 |
|---|---|
| minimal（默认）· standard · code · cordis | `<dsh 安装目录>/config/agent-presets/<模式>/agent.cordis.yml` —— 找 **`- id: persona`** 条目，改它的 **`config.text`** |
| router-spec · router-standard | `~/.dsh/.agent-presets/<模式>/router-core.mjs` —— `personaFor()` = `withChineseCoT(rawPersonaFor(mode, modelId))` |

`<dsh 安装目录>` = `C:\Users\z\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh`（**这份现在没在跑**）

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
