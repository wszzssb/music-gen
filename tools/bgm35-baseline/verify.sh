#!/usr/bin/env bash
# 一键核对：BGM35 家族产物有没有被动过
#   产物（第一节，共 12 项）不一致 = **FAIL**
#   共享代码（第二节）= 只提示漂移（代码本来会演进）
set -u
export PYTHONIOENCODING=utf-8 LANG=C.UTF-8
HERE="$(cd "$(dirname "$0")" && pwd)"
R="$(cd "$HERE/../.." && pwd)"
cd "$R" || exit 1
BASE="$HERE/baseline.sha256"
[ -f "$BASE" ] || { echo "缺 $BASE"; exit 2; }

TMP="$(mktemp)"
# ⚠ 第一节的 sec 必须显式初始化，否则字段错位、整节被跳过（第一版就是这么"永远 PASS"的）
awk 'BEGIN{sec=1} /^## 共享代码/{sec=2; next} /^#/{next} NF{print sec" "$0}' "$BASE" > "$TMP"
n_prod=$(grep -c '^1 ' "$TMP" || true)
if [ "${n_prod:-0}" -lt 12 ]; then
  echo "FAIL：基线清单只解析出 $n_prod 项产物（应 12）—— **核对脚本自己坏了**，先修它"
  rm -f "$TMP"; exit 2
fi

fail=0; drift=0
while read -r sec hash path; do
  [ -z "${path:-}" ] && continue
  path="${path#\*}"                     # MSYS sha256sum 默认二进制模式，路径带 `*`
  if [ ! -f "$path" ]; then
    printf '  ✗ 缺失   %s\n' "$path"; fail=$((fail+1)); continue
  fi
  now=$(sha256sum "$path" | cut -d' ' -f1)
  if [ "$now" != "$hash" ]; then
    if [ "$sec" = "1" ]; then printf '  ✗ 被改动 %s\n' "$path"; fail=$((fail+1))
    else printf '  ~ 代码漂移 %s\n' "$path"; drift=$((drift+1)); fi
  fi
done < "$TMP"
rm -f "$TMP"

if [ "$fail" -eq 0 ]; then
  echo "PASS：BGM35 家族产物**逐字节未变**（核对了 $n_prod 项产物；代码漂移 $drift 项，仅提示）"
else
  echo "FAIL：$fail 项与基线不一致 —— 有人动过用户认可的成品，先查再动"
fi
exit $fail
