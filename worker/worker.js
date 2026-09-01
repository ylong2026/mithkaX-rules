// Cloudflare Worker: Telegram 转发 -> 写入 mithkaX-rules 仓库的 pending/
// 全程 serverless：不依赖你的电脑、不依赖你自己的服务器，免费额度足够。
//
// 职责：
//   1) 收到 Telegram 转发（message）-> 归一化后写入 GitHub pending/<date>/<hash>.json
//      （同一条广告被多人转发 -> 累加 reporter_ids，命中 AUTO_CANDIDATE_MIN_REPORTERS 即高置信）
//   2) 收到类别选择按钮回调（callback_query）-> 把 chosen 类写回该 pending 文件
//   3) GitHub Actions 定时把 pending/ 处理成 rules.json 并推送（见 .github/workflows/process.yml）
//
// 环境变量（在 Cloudflare 控制台 / wrangler secret 设置）：
//   BOT_TOKEN       Telegram BotFather 给的 token
//   GITHUB_PAT      有 repo 权限的 GitHub PAT（仅用于写 pending/）
//   REPO            "ylong2026/mithkaX-rules"
//   BRANCH          "main"
//   WEBHOOK_SECRET  Telegram webhook 的 secret_token（防伪造请求）

const CATEGORIES = [
  "airport", "gambling", "adult", "phishing",
  "scam", "selling", "finance", "proxy", "spam_link",
];

function b64encode(str) {
  return btoa(unescape(encodeURIComponent(str)));
}
function b64decode(str) {
  return decodeURIComponent(escape(atob(str)));
}
function chunk(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

async function tgApi(token, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json().catch(() => null);
}

async function ghGet(env, path) {
  const repo = env.REPO || "ylong2026/mithkaX-rules";
  const branch = env.BRANCH || "main";
  const r = await fetch(
    `https://api.github.com/repos/${repo}/contents/${path}?ref=${branch}`,
    {
      headers: {
        Authorization: `Bearer ${env.GITHUB_PAT}`,
        "User-Agent": "mithkaX-rules-worker",
        Accept: "application/vnd.github+json",
      },
    }
  );
  if (!r.ok) return null;
  return r.json().catch(() => null);
}

async function ghPut(env, path, content, sha, message) {
  const repo = env.REPO || "ylong2026/mithkaX-rules";
  const branch = env.BRANCH || "main";
  const body = { message, content: b64encode(content), branch };
  if (sha) body.sha = sha;
  const r = await fetch(
    `https://api.github.com/repos/${repo}/contents/${path}`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${env.GITHUB_PAT}`,
        "Content-Type": "application/json",
        "User-Agent": "mithkaX-rules-worker",
      },
      body: JSON.stringify(body),
    }
  );
  return r.ok;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text)
  );
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

async function handleMessage(env, msg) {
  const text = msg.text || msg.caption || "";
  if (text.length < 10) return; // 太短无法提取特征，忽略
  const fromId = msg.from ? msg.from.id : null;
  const date = new Date().toISOString().slice(0, 10);
  const hash = await sha256Hex(text);
  const path = `pending/${date}/${hash}.json`;

  const existing = await ghGet(env, path);
  let data;
  if (existing) {
    data = JSON.parse(b64decode(existing.content));
    const set = new Set(data.reporter_ids || []);
    if (fromId) set.add(fromId);
    data.reporter_ids = [...set];
    data.updated_at = new Date().toISOString();
  } else {
    data = {
      text,
      from_id: fromId,
      reporter_ids: fromId ? [fromId] : [],
      hash,
      forward_origin: msg.forward_origin ? "forward" : "direct",
      created_at: new Date().toISOString(),
    };
  }
  await ghPut(env, path, JSON.stringify(data, null, 2), existing ? existing.sha : null, `spam: ${hash}`);

  // 回一个类别选择按钮（仅校正已有类；新类由 Owner 审批）
  if (fromId) {
    const kb = chunk(
      CATEGORIES.map((c) => ({
        text: c,
        callback_data: `cat:${date}:${hash}:${c}`,
      })),
      3
    );
    kb.push([{ text: "跳过(自动归类)", callback_data: `cat:${date}:${hash}:__skip__` }]);
    await tgApi(env.BOT_TOKEN, "sendMessage", {
      chat_id: fromId,
      text: "已收到举报 ✅ 可选类别（不点也行，后台自动归类）：",
      reply_markup: { inline_keyboard: kb },
    });
  }
}

async function handleCallback(env, cb) {
  const m = (cb.data || "").match(/^cat:(.+?):(.+?):(.+)$/);
  if (!m) return;
  const date = m[1];
  const hash = m[2];
  const cat = m[3];
  const path = `pending/${date}/${hash}.json`;
  const f = await ghGet(env, path);
  if (f && cat !== "__skip__") {
    const pd = JSON.parse(b64decode(f.content));
    pd.category = cat;
    pd.updated_at = new Date().toISOString();
    await ghPut(env, path, JSON.stringify(pd, null, 2), f.sha, `cat: ${hash} -> ${cat}`);
  }
  await tgApi(env.BOT_TOKEN, "answerCallbackQuery", {
    callback_query_id: cb.id,
    text: cat === "__skip__" ? "已跳过，后台自动归类" : `已标记为 ${cat}`,
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("mithkaX-rules worker", { status: 200 });
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (env.WEBHOOK_SECRET && secret !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const upd = await request.json().catch(() => null);
    if (!upd) return new Response("bad request", { status: 400 });
    try {
      if (upd.message) await handleMessage(env, upd.message);
      else if (upd.callback_query) await handleCallback(env, upd.callback_query);
    } catch (e) {
      return new Response("error: " + e.message, { status: 500 });
    }
    return new Response("ok", { status: 200 });
  },
};
