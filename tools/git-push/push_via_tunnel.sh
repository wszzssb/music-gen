#!/usr/bin/env bash
# push_via_tunnel.sh —— `git push` 连不上 github.com 时的绕行推送（原理与实测见同目录 README.md）
#
# 三步：先试直连 → 不通就挑一个可达的 GitHub IP → 起本地 TCP 转发器把连接固定转发过去再推。
# 从任意目录运行都可以（仓库路径用 git rev-parse 自己找）。
#
# 用法：
#   bash tools/git-push/push_via_tunnel.sh                  # 默认 origin main
#   bash tools/git-push/push_via_tunnel.sh origin dev        # 指定远端与分支
#   FORCE_TUNNEL=1 bash tools/git-push/push_via_tunnel.sh    # 跳过直连，直接走转发
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REMOTE=${1:-origin}
BRANCH=${2:-main}
PORT=${PORT:-18443}
# 常见可达的 GitHub IP（会逐个探活；GitHub 会调整，全不可达就放弃）
CANDIDATES=${CANDIDATES:-"140.82.114.3 20.27.177.113 140.82.113.4 20.205.243.168 20.205.243.166"}

REPO=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "不在 git 仓库里"; exit 1; }
cd "$REPO" || exit 1

# python：优先仓库自带的 venv，否则系统 python
PY=""
for c in "$REPO/.venv/Scripts/python.exe" "$REPO/.venv/bin/python" python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PY=$c; break; }
done
[ -z "$PY" ] && { echo "找不到 python（转发器需要它）"; exit 1; }

echo "仓库     : $REPO"
echo "本地 HEAD: $(git rev-parse --short HEAD)   远端 $REMOTE/$BRANCH: $(git rev-parse --short "$REMOTE/$BRANCH" 2>/dev/null || echo '?')"
echo "待推提交 : $(git rev-list --count "$REMOTE/$BRANCH..HEAD" 2>/dev/null || echo 0)"

echo "--- ① 先试直连 ---"
if [ "${FORCE_TUNNEL:-0}" = "1" ]; then
  echo "（FORCE_TUNNEL=1：跳过直连）"
elif timeout 60 git push "$REMOTE" "$BRANCH"; then
  echo "✅ 直连推送成功"
  exit 0
fi
echo "直连没成 → 改走本地转发通道"

echo "--- ② 挑可达的 GitHub IP ---"
USE=""
for ip in $CANDIDATES; do
  ok=0
  for i in 1 2; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 4 --max-time 6 \
             --resolve github.com:443:"$ip" https://github.com 2>/dev/null)
    case "$code" in 200|301|302|400) ok=$((ok+1));; esac
  done
  echo "  $ip → $ok/2"
  [ "$ok" = "2" ] && { USE=$ip; break; }
done
[ -z "$USE" ] && { echo "❌ 候选 IP 都不可达 —— 这更像整体网络不通，不是本机这条问题"; exit 1; }
echo "  选用 $USE"

echo "--- ③ 起转发器并推送 ---"
"$PY" "$HERE/tcp_forward.py" --listen "127.0.0.1:$PORT" --to "$USE:443" > /tmp/git_push_tunnel.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
sleep 2
cat /tmp/git_push_tunnel.log
git -c http.proxy="http://127.0.0.1:$PORT" push "$REMOTE" "$BRANCH"
code=$?
echo "推送退出码=$code"
exit $code
