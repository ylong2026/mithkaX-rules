#!/usr/bin/env bash
# 一键部署 mithkaX-rules Worker 到 Cloudflare（经 CF API，无需 wrangler/本地工具）
# 用法：
#   CF_API_TOKEN=xxx BOT_TOKEN=xxx GITHUB_PAT=xxx bash deploy.sh
# 三个值都来自你（粘贴给我即可），脚本本身不含任何秘密。
set -e

: "${CF_API_TOKEN:?缺 CF_API_TOKEN}"
: "${BOT_TOKEN:?缺 BOT_TOKEN}"
: "${GITHUB_PAT:?缺 GITHUB_PAT}"

# workers.dev 子域名必须小写，故脚本名用小写
SCRIPT_NAME="mithkax-rules-bot"
REPO_DIR="/Users/rui/WorkBuddy/2026-09-01-10-38-23/mithkaX-rules"
WORKER_JS="$REPO_DIR/worker/worker.js"

# 现场生成 webhook 密钥（随机），避免硬编码
WEBHOOK_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")

echo "=== 1) 取 account_id ==="
if [ -n "$ACCOUNT_ID" ]; then
  echo "使用提供的 ACCOUNT_ID 覆盖（无需 Account:Read 权限）"
else
  ACCOUNT_ID=$(curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
    https://api.cloudflare.com/client/v4/accounts \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result'][0]['id'])")
fi
echo "account_id=$ACCOUNT_ID"

echo "=== 2) 上传 Worker 脚本（multipart，ES module 格式）==="
# worker.js 是 ES module（export default），必须用 multipart 带 metadata 上传，
# 否则 CF 按 Service Worker 格式解析会报 "Unexpected token 'export'"。
curl -s -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$SCRIPT_NAME" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -F 'metadata={"main_module":"worker.js"};type=application/json' \
  -F "worker.js=@$WORKER_JS;type=application/javascript+module" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('upload:', 'OK' if d.get('success') else d)"

echo "=== 3) 设置 3 个加密 secret ==="
# 注意：CF 的 PUT secret 端点是 .../secrets（不带名字），body 需含 name/type/text
for kv in "BOT_TOKEN:$BOT_TOKEN" "GITHUB_PAT:$GITHUB_PAT" "WEBHOOK_SECRET:$WEBHOOK_SECRET"; do
  name="${kv%%:*}"; val="${kv#*:}"
  echo "  setting $name ..."
  curl -s -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$SCRIPT_NAME/secrets" \
    -H "Authorization: Bearer $CF_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"text\":\"$val\",\"type\":\"secret_text\"}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print('   ->', 'OK' if d.get('success') else d)"
done

echo "=== 3.5) 启用该脚本的 workers.dev 子域路由（否则访问 404）==="
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$SCRIPT_NAME/subdomain" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('subdomain route:', 'OK' if d.get('success') else d)"

echo "=== 4) 取 workers.dev 子域，拼出 Worker URL ==="
SUB=$(curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['subdomain'])")
WORKER_URL="https://$SCRIPT_NAME.$SUB.workers.dev"
echo "worker_url=$WORKER_URL"

echo "=== 5) 把 Telegram 机器人指向 Worker（设 webhook）==="
curl -s "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=$WORKER_URL&secret_token=$WEBHOOK_SECRET"
echo

echo
echo "================ 部署完成 ================"
echo "Worker URL     : $WORKER_URL"
echo "WEBHOOK_SECRET : $WEBHOOK_SECRET  (请记下，如有需要排查用)"
echo "下一步：在 Telegram 转发一条广告给机器人 -> GitHub Actions 每4h自动处理"
