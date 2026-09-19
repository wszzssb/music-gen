# git 推送绕行（GitHub 连不上时用这个）

**什么时候用它**：`git push` 报 `Could not connect to github.com:443` /
`Failed to connect to github.com port 443 after N ms`，重试几轮也不通。

**一句话结论**：实测这类失败**不是仓库的问题、也不是 DNS 的问题** ——
是**某一个目标 IP 的 443 被中间设备丢弃**，而 GitHub 还有一批 IP 是可达的。

## 实测形态（2026-09-19，本机一轮完整对照）

| 观测 | 数据 | 排除了什么 |
|---|---|---|
| DNS | 运营商 DNS 与 `8.8.8.8` / `223.5.5.5` / `119.29.29.29` **四个一致返回 `20.205.243.166`**，连续 5 次稳定、9–46ms | **不是 DNS 污染/不稳** |
| ICMP | `ping 20.205.243.166` **26/26 成功、0% 丢包、61–112ms** | **链路是通的** |
| TCP 443 | 同一时段 `curl https://github.com` **50 次里只成功 1 次**（失败形态 `conn=0.000`、`total≈4.0s` ＝ SYN 被丢，不是被拒绝） | 卡在 **TCP 握手** |
| 同段另一 IP | `20.205.243.168`（就是 `api.github.com`）**30/30 全 200** | **不是整段封锁** |
| **SNI 交叉** | `.166`+SNI`api.github.com` → 失败；`.168`+SNI`github.com` → **连上了** | **失败随 IP 走、不随 SNI 走**（别往"域名/SNI 被拦"查） |
| 其他通道 | `github.com:22`、`ssh.github.com:443`、`raw.githubusercontent.com` 同时都可达 | 见下面"长期方案" |

## 用法

```bash
bash tools/git-push/push_via_tunnel.sh                    # 默认 origin main
bash tools/git-push/push_via_tunnel.sh origin dev          # 指定远端与分支
FORCE_TUNNEL=1 bash tools/git-push/push_via_tunnel.sh      # 跳过直连，直接走转发
```

从任意目录运行都行（仓库路径用 `git rev-parse --show-toplevel` 自己找）。
流程：**先试直连** → 不通就**逐个探活候选 IP** → 起本地转发器（`tcp_forward.py`）
把连接固定转发到可达 IP → `push` → 自动关掉转发器。

> **零改动的替代办法**：**隔 15 秒重试**。实测 8 轮探活全 `000` 之后，慢重试**第 3 轮**就推上去了
> —— 这类丢弃本身是间歇的（同一 IP 偶发放行）。

## 原理，以及两个必须知道的坑

TLS 仍是**端到端**的：SNI 与证书都由 git 和真 GitHub 完成，这里只换了**目标 IP**（Host/SNI 不变），
所以不存在"证书不匹配"或"中间人"的问题。

- **坑 1（转发器会静默失效）**：git 走 http 代理访问 https 时，会先发 `CONNECT host:443`。
  转发器**必须自己回 `HTTP/1.1 200 Connection established` 再裸转发** ——
  原样把这行转给 GitHub 会被当成垃圾请求。
- **坑 2（怎么证明它真生效）**：拿**黑洞地址**当对照 —— 转发到 `192.0.2.1:443`（TEST-NET-1 保留段）
  **必须失败**（`fatal: ... Proxy CONNECT aborted`、退出码 128），转发到可达 IP **必须成功**。
  少了这个对照，"经转发器成功"可能只是碰巧直连成功 —— **等于没验**。

## 边界（诚实）

- 候选 IP 写在脚本里（`CANDIDATES` 可覆盖），GitHub 会调整；脚本**逐个探活**，
  但**全不可达时会直接放弃** —— 那更像整体网络问题，不是本机这条。
- **只解决"个别目标 IP 的 443 被丢"这一类**；如果是 DNS 解析不出 github.com，
  它没用（那时先查 `nslookup github.com` 与 `nslookup github.com 8.8.8.8`）。
- **长期方案是 SSH 推送**（`github.com:22` 实测可达）：公钥要在 GitHub 账号登记一次；
  注意用本机存的 `gho_` token 走 `api.github.com` **自动登记会 404**
  （该 token 的 scope 只有 `gist, repo, workflow`，缺 `write:public_key` —— GitHub 用 404 隐藏无权端点）。
- **没验到的**：至今没能在"`.166` 正被丢"的时段当场验证转发器救场（试的时候恰好是放行窗口）；
  这一条是"逻辑 + 通道已验证"（`ls-remote`、`push --dry-run`、黑洞对照都过了）。

症状台账 → `PITFALLS.md` 212 · 命令速查 → `CHEATSHEET.md`。
