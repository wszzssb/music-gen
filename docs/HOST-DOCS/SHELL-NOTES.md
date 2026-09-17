<!-- ⚠ 这是**备份**，不是生效副本：真正被 DSH 加载的是宿主上的
     `C:/Users/z/.dsh/docs/SHELL-NOTES.md`。要改请改那份（改完新会话生效），再重跑 `sync_host_docs.py` 同步过来。 -->

# 本机 shell：踩坑清单与原因（AGENTS.md 的展开版）

> AGENTS.md 里只留"铁律 + 指针"，**证据与原因**在这里。
> **只在怀疑"这毛病是不是环境的锅"时才读。**
>
> ⚠ 本文件每一条都**当场实测过**（2026-09-17，Windows PowerShell 5.1.26100.8655 / Desktop）。
> 其中一条是**修正**：早先记录里"`Set-Content -Encoding UTF8` 会把中文变成乱码"**与实测不符**
> （实测是带 BOM 的正确 UTF-8，内容没坏）—— 见下表第 2 行。
> **教训：写进文档的"实测"必须当场跑一遍，转述会失真。**

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
