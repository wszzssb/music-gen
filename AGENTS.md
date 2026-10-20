# AGENTS.md —— 你是被丢进这个目录的 AI，先读这 60 行

> 这是 **BGM Studio**（作曲 / 扒谱 / 渲染 / 体检工具链）。仓库根 = 你当前所在目录。
> 别先读 `README.md`（23.7k）或 `PITFALLS.md`（巨大）——照下面三步走。

## 三步开工（复制即可）

```powershell
$py = <仓库根>\.venv\Scripts\python.exe

& $py scripts\ai_entry.py            # ① 定位：目录地图 / 曲库 / 面板 / 红线
& $py scripts\ai_entry.py doctor     # ② 体检：venv / 音源 / ogg / 主题包 / 面板
& $py scripts\ai_entry.py brief gorgeous   # ③ 拿"能写什么"（和弦词表 / arr 键 / 示例结构表）
```

然后：

```powershell
& $py scripts\ai_entry.py new 140_x --theme gorgeous --seconds 120
& $py scripts\ai_entry.py plan 140_x --plan p.json --report   # 套外部设计表 + 逐段×逐轨报表
& $py scripts\ai_entry.py render 140_x
& $py scripts\ai_entry.py check 140_x                          # 默认 --fast（秒级）
```

> `check --deep` 会跑全库（**2~3 分钟**），交付前才用。面板：`http://127.0.0.1:8765`。

## 你在哪 / 东西在哪

| 路径 | 是什么 |
|---|---|
| `scripts\` | 工具。核心：`new_song` `expand_sections` `melody_gen` `make_song` `check_song` |
| `refs\themes\*.json` | **15 个主题模板包 = 写歌的合规依据（LLM 写大纲时 spec 带 theme 即可）**（各由 ≥8 首真实 MIDI 聚合） |
| `songs\<曲>\` | 曲库。一首 = `song.json` + `render.json` + `.mid` + `_sf.ogg` + `notes.md` |
| `docs\` | 细则。按需：`STANDARD` `THEME-PACK` `SONG-FORMAT` `IMITATE-PATH` `RESTORE-METHOD` |
| `studio\` | 面板（可视化听/改，`studio\start.cmd`） |

## 三条红线（守卫会拦，绕不过）

1. **新歌只写 `song.json` / 结构表，不改代码**；依据只能是主题包（或带 URL 的权威数据）。
   不许拿"某首音频/某首生成的曲子"当模板。
2. **改 `song.json` 前先记 SHA256**；**改必须分段**（逐段/逐小节），不许整曲一刀切；
   一次只改一个维度（否则"好了"也归因不了）。
3. **交付前跑 `check_song`**；**"没达标"不许说成"达标"**；测不到的项（表情/好听度/过渡）
   必须逐条写"没验证什么"。

## 想更细地编排（给 LLM 的接口）

- `ai_entry.py brief <主题>` → 合法和弦、每段 `arr` 键与**取值范围**、`prog`（段级换音色）、
  `shift`（段级移八度）、鼓件与 16 格、一张能直接跑的示例结构表。
- 结构表里写 **`"strict": true`** → 引擎**不再**让步/移八度夹取/去重，只报"本来会怎么动"；
  plan 没点名的 15 个引擎启发式也一律不跑（写进 `patterns` 才会跑）。
- `plan ... --report` 会打**逐段×逐轨音符数** + "要了这层却 0 音"清单 —— 用来核对指令是否真落地。
- 已知缺口：plan 路径**不会自动**跑 `new_song.fix_melody_register`（旋律可能比和弦高 26 半音，
  `check_song` 会报）；以及 `strict` 尚未有独立自检守卫。

## 出问题先看哪

| 症状 | 先跑 |
|---|---|
| 编译/数据错 | `check_song.py <曲>`（`--fast` 秒级） |
| 听感怪、说不出 | `selfcheck.py <曲>`（人话离群清单）+ `report_sections.py` |
| 某层没声音 | `expand_sections.py <曲> --plan p.json --report`（看"指令未生效"） |
| 工具本身坏了 | `selftest.py --fast`（全量自检；**别与 mutation_check 并发**） |
