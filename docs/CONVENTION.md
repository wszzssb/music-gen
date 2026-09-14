# 文档约定：以后新增内容往哪放（**每次新增/修改都照这份做**）

**多对话共用 → "往哪写"必须有唯一答案，且由守卫强制**。本文档自身也在预算/指针守卫之下。

## 1. 五条硬原则

1. **定义紧贴实现**：能由代码回答的别抄进文档（风格清单 → `new_song.py --list-styles`、
   参数 → `LIMITS`/`STYLES`）。**抄一份 = 埋一处漂移**。
2. **路由表是唯一入口**：新增一类文档必须同时加一行，否则等于不存在（守卫 FAIL）。
3. **先量再拆**：用 `token_audit.py` 估；**加不进去 ≠ 该拆**，也可能是该挪进 `--help` 或该删。
4. **不许空话放行**：豁免必须有实质理由（理由空白 = 没写，自检会拦）。
5. **每份文档都有预算**：超了 FAIL；调上限要给理由（如"台账增长 → 拆归档"），
   **不许为消警告直接抬上限**。

## 2. 往哪放（决策表）

| 我要加… | 放哪 | 体量 | 还要做 |
|---|---|---|---|
| 一条**坑** | `PITFALLS.md` 追加 | ≤8000 | 无需改路由 |
| 一次**命令/开关** | `CHEATSHEET.md` | ≤1600 | 脚本 `__doc__` 里也要有 |
| **song.json 字段/风格/参数** | `docs/SONG-FORMAT.md` | ≤2000 | 引擎里先有实现，再补速查 |
| 一个新**工具** | `README.md` 清单 + `__doc__` | ≤5600 | 加自检 + **配变异用例** |
| **铁律/口径** | `SKILL.md` | ≤1700 | **删等价内容腾位** |
| 一段**开发经过/决策**（写代码才看） | `HISTORY.md` | 无上限 | 无需改路由 |

## 3. 守卫（改完必跑，会自动抓下面 4 类错）

| 守卫（自检项） | 抓什么 | 变异用例 |
|---|---|---|
| `docs_budget_and_skill_intact` | 文档超预算 / SKILL 丢 frontmatter | — |
| `skill_routes_resolve` | 路由表指向不存在的文档（静默失效：agent 读不到 → 只好整篇读 README） | 第 52 条 |
| `docs_paths` | 文档引用不存在的 `scripts/*.py`；**文档之间的 `.md` 指针腐烂** | 改名实验已验证 |
| `docs_host_classification` | **分类与路径不自洽**（仓库文件被标宿主级 → 缺失静默跳过；反之换机器崩） | 第 54 条 |
| `track_ranges_musical` | 轨的音域超出乐器合理区间（如低音成次声波） | 第 57 条 |
| `song_spec_sync` | `spec.json` 与 `song.json` 漂移（复现失效） | 第 58 条 |
| `checks_have_assertions` | 只有打印、从不 FAIL 的"装饰性检查" | — |

**新增检查 = 必须配注入用例**（`mutation_check.py`）：坏不了的检查等于没检查。

## 4. 常用流程（照抄）

**A. 加一个工具**：① `scripts/<名>.py`（`__doc__` 写清何时用/怎么用）② `README.md` 清单加一行
③ `selftest.py` 加一项（**要有断言**）+ `mutation_check.py` 加注入用例 ④ 三项全绿

**B. 加一条坑**：`PITFALLS.md` 编号追加 → 若属"每次都会犯"，压一行进 `SKILL.md`（删等量腾位）

**C. 加一份新文档**（判据：**同一步骤一份文档能覆盖**）：① `SKILL.md` 路由表加一行（含 ≈体量）
② `token_audit` 的 DOCS + LIMITS 加一项 ③ `docs_paths` 列表加上它 ④ 自检 + 变异全绿

## 5. 快速自查

```powershell
$py = "<工具链根>/venv/python.exe"; cd <工具链根>
& $py scripts\token_audit.py          # 预算余量
& $py scripts\selftest.py --fast      # 守卫（含路由/指针/预算）
& $py scripts\mutation_check.py       # 新检查是否"坏得起来"
```

> 判据：**只读这一份就知道往哪写、动哪些守卫、怎么验**。读完还得问 = 约定漏了，补进来。

## 6. 宿主级 vs 仓库级（**别混**）

| 类别 | 谁提供 | 例子 | 自检怎么对待 |
|---|---|---|---|
| **仓库级** | 本仓库 | `README.md`、`docs/*.md`、`CHEATSHEET.md`、`PITFALLS*.md` | 缺失 = 真问题，直接 FAIL |
| **宿主级** | 用户目录（技能/全局约定） | `~/.dsh/AGENTS.md`、`~/.dsh/skills/bgm-studio/SKILL.md` | 缺失 = **跳过校验并提示**，不崩；本机要强制 → `DSH_REQUIRE_SKILL=1` |

**为什么**：它们不由仓库分发。自检若硬断言存在，换台机器就会 `FileNotFoundError` 崩掉整套自检。
标注位置：`token_audit.HOST_LEVEL`（分类与路径的**自洽性**由 `docs_host_classification` 守卫）。

**新增文档先回答**：跟着仓库走 → 仓库级；跟着用户环境走 → 宿主级。
