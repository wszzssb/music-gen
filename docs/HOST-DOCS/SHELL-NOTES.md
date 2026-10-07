<!-- ⚠ 这是**备份**，不是生效副本：真正被 DSH 加载的是宿主上的
     `C:/Users/z/.dsh/docs/SHELL-NOTES.md`。要改请改那份（改完新会话生效），再重跑 `sync_host_docs.py` 同步过来。 -->

# 本机 shell：踩坑清单与原因（AGENTS.md 的展开版）

> AGENTS.md 里只留"铁律 + 指针"，**证据与原因**在这里。
> **只在怀疑"这毛病是不是环境的锅"时才读。**
>
> ⚠ 本文件每一条都**当场实测过**（初始那批 2026-09-17，Windows PowerShell 5.1.26100.8655 / Desktop；
> 后续新增的条目各自在正文里标了日期）。
> 其中一条是**修正**：早先记录里"`Set-Content -Encoding UTF8` 会把中文变成乱码"**与实测不符**
> （实测是带 BOM 的正确 UTF-8，内容没坏）—— 见下表第 2 行。
> **教训：写进文档的"实测"必须当场跑一遍，转述会失真。**

## 我实际犯过的错（写下来防再犯）

**2026-09-17，同一天里的第二次**：刚把"多步操作一律落文件"写进 AGENTS.md，
转头就在 `pwsh` 里**内联**了一段带 heredoc 的长命令：

```powershell
& 'D:\software\Git\bin\bash.exe' -lc 'cd /d/software/skill/music-gen && export PYTHONIOENCODING=utf-8 && python - <<PYEOF
...
PYEOF'
```

后果：**引号被吞 + heredoc 失效** ——
`warning: here-document at line 1 delimited by end-of-file (wanted 'PYEOF')`，
python 收到的是被拆碎的参数、直接 SyntaxError。**正是本文件第 1 节写的坑。**

**为什么又犯**：图快 —— 觉得"就这一次、就几行"，省掉"先写个 .sh"那一步。

**所以规矩必须写成"可自查"的形状**（软措辞拦不住，例如"只留最简单的调用"这种，
每次都能给自己找到"这次算简单"的理由）：

> `pwsh` 命令里**出现 `&&` `|` `>` `<<` `"` `$` 任何一个 → 停下，改成 `.sh` 脚本。**

正确调用**永远只有一个形状**：

```powershell
& 'D:\software\Git\bin\bash.exe' 'D:\test\llm_direct\shells\xxx.sh'
```

（`pwsh` 工具本身**躲不掉** —— bash.exe 也要由它启动。要禁的不是这个工具，
而是"在它里面内联多步逻辑"。）

## 为什么不能用 pwsh 工具

这台机器上 `pwsh` 工具**实际执行的是 Windows PowerShell 5.1**（`$PSVersionTable` ≈ `5.1.26100.8655`，
`PSEdition=Desktop`），不是 pwsh 7。逐条实测：

| 症状 | 实测到的原因 |
|---|---|
| `read` 工具把输出文件判成 **binary** | `>` 重定向写出 **UTF-16LE**：首字节 `FF FE`，且 ASCII 字符后面跟着 `00` |
| `json.load` 报 `Unexpected UTF-8 BOM (decode using utf-8-sig)` | `Set-Content -Encoding UTF8` 写的是**带 BOM 的 UTF-8**（首字节 `EF BB BF`）—— **内容没坏**，BOM 才是问题；`encoding='utf-8-sig'` 可解（工具链 `read_json` 就是这么兜的） |
| 报"参数不存在" | `-AsByteStream` 在 5.1 里没有：`A parameter cannot be found that matches parameter name 'AsByteStream'` |
| 传数组报错 | `-Filter` 只收字符串：`Cannot convert 'System.Object[]' to the type 'System.String' required by parameter 'Filter'` |
| 命令参数被吃掉 | 半角双引号被吞：嵌套调用报 `cannot find a positional parameter that accepts argument 'B'`（`"A B"` 被拆成两个参数） |
| 中文输出乱码 | 控制台代码页（未单独复验，属现象描述） |

## 改用 Git Bash

```bash
& 'D:\software\Git\bin\bash.exe' '<脚本.sh>'
```

bash 5.3.15，已实测：中文输出正常、引号原样传递、重定向写出**真 UTF-8**、heredoc 可用。
**WSL 未安装发行版，不可用。**

## Git Bash 自己的两个坑（同样实测过）

- **`cmd.exe /c` 会被 MSYS 做路径转换**：
  `cmd.exe /c echo hi` 输出的是 **cmd 的交互欢迎信息**（`/c` 被当成路径，参数没传进去），
  必须写 **`cmd.exe //c echo hi`** 才得到 `hi`。
- **没有 `bc`**：`command -v bc` 查不到，调用直接报 `bc: command not found` → 算术用 python 或 awk。

## 附：`| grep` 吞掉一切（这条不是环境问题，是习惯问题）

```bash
失败的命令 | grep -c .        # 退出码是 grep 的 1，不是失败命令的 7（实测）
失败的命令 >/dev/null 2>&1    # 才是 7
```
管道会把上游的退出码与 stderr 一起吞掉 —— 排查时**先看退出码，再决定要不要过滤**。

## 附：`.ps1` 里写中文常量 → 被 PS 5.1 当 GBK 读（2026-10-07 实测）

**和上面"改用 Git Bash"那一节配套**：多步操作落成 `.ps1`、再用
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File '<x.ps1>'` 执行，是常用形状；
**但 `write` / `edit` 工具写出的 `.ps1` 是无 BOM 的 UTF-8，`-File` 会按本地 ANSI(GBK) 解析。**

实测症状（清 C/D 盘那次）：

| 脚本里写的 | PS 5.1 实际拿到的 |
|---|---|
| `D:\software\A绘世启动器\...` | `D:\software\A缁樹笘鍚姩鍣╘\...` |
| `D:\game\Rance10 日不落0.484B` | `D:\game\Rance10 鏃ヤ笉钀?.484B` |

**危险的地方不是报错，是"静默跳过"**：`Test-Path` 对烂路径返回 `false`，
`Get-Item` 返回空 → 脚本走进 `SKIP (missing)` 分支、**退出码仍是 0**，
只有输出里那一行 `<absent>` 露馅。当脚本里有"删/改"动作时，这种失真会让**守卫判断**变成假的。

**三种解法**（前两种是 win-forensics 已经固化的，别自己再造）：

1. **走它的 `run.sh`**（会自动给 `.ps1` 补 BOM，并按扩展名选解释器）：
   `& 'D:\software\Git\bin\bash.exe' 'D:\software\win-forensics\scripts\run.sh' '<脚本.ps1>' [参数…]`
2. **写完补跑一次**：`python D:\software\win-forensics\scripts\fix_ps1_encoding.py`
3. **脚本里不写中文常量**，从文件系统发现路径。本次采用的是第 3 种（因为那些 `.ps1` 只做删除、且要进提权流程）。
   附带一条：**中间段通配在 PS 5.1 下没匹配到** ——
   `Get-ChildItem 'D:\software\*\sd-webui-aki-v4.11.1-cu128\.cache' -Directory -Force` 返回空，
   改成 `foreach ($d in (Get-ChildItem 'D:\software' -Directory)) { Test-Path (Join-Path $d.FullName '...') }`
   才找到（同一个目录，5.3 GB）。**所以通配符也别全信，要有一处独立回读。**

**配套自查**：脚本里每个"跳过/没找到"分支都必须**显式写进日志**（本次就是靠输出里的
`<absent>` 才发现两个目标根本没被处理），否则"跑完了、退出码 0"会被误读成"做完了"。
