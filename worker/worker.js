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

// ---- i18n：按钮/提示语按用户语言显示，callback_data 仍用英文类别 ID ----
const LANGS = {
  zh: {
    received: "已收到举报 ✅ 可选类别（不点也行，后台自动归类）：",
    skip: "跳过(自动归类)",
    marked: (cat) => `已标记为 ${cat}`,
    skipped: "已跳过，后台自动归类",
    langPrompt: "请选择语言：",
    langSet: "已切换为中文",
    start: "👋 欢迎使用 mithkaX 广告过滤机器人！\n\n转发一条广告消息给我，我会自动提取特征并加入社区规则库。\n\n命令：\n/help — 使用说明\n/lang — 切换语言",
    help: "📖 使用说明\n\n1. 在 Telegram 里看到广告/诈骗消息\n2. 转发给我（@FuckTelegramAD_bot）\n3. 可选：点按钮选择类别（不点也行，后台自动归类）\n4. 规则每 4 小时自动处理进规则库，App 端自动拉取\n\n命令：\n/lang — 切换语言\n/help — 查看此说明",
    categoryChosen: (label) => `✅ 已标记为 ${label}`,
    skippedChosen: "⏭️ 已跳过，后台自动归类",
    langChosen: "✅ 语言已切换为中文",
    categories: {
      airport: "✈️ 机场/VPN",
      gambling: "🎰 赌博",
      adult: "🔞 色情",
      phishing: "🎣 钓鱼",
      scam: "💸 诈骗",
      selling: "🛒 卖货/招代理",
      finance: "💰 非法金融",
      proxy: "🔧 代办/解封",
      spam_link: "🔗 推广链接",
    },
  },
  en: {
    received: "Report received ✅ Pick a category (optional, auto-classify if skipped):",
    skip: "Skip (auto-classify)",
    marked: (cat) => `Marked as ${cat}`,
    skipped: "Skipped, will auto-classify",
    langPrompt: "Choose language:",
    langSet: "Switched to English",
    start: "👋 Welcome to mithkaX ad-filter bot!\n\nForward an ad message to me and I'll extract patterns into the community rule set.\n\nCommands:\n/help — How to use\n/lang — Switch language",
    help: "📖 How to use\n\n1. See an ad/scam message in Telegram\n2. Forward it to me (@FuckTelegramAD_bot)\n3. Optional: tap a category button (skip = auto-classify)\n4. Rules are processed every 4h, the app pulls automatically\n\nCommands:\n/lang — Switch language\n/help — Show this help",
    categoryChosen: (label) => `✅ Marked as ${label}`,
    skippedChosen: "⏭️ Skipped, will auto-classify",
    langChosen: "✅ Language switched to English",
    categories: {
      airport: "✈️ VPN/Proxy",
      gambling: "🎰 Gambling",
      adult: "🔞 Adult",
      phishing: "🎣 Phishing",
      scam: "💸 Scam",
      selling: "🛒 Selling",
      finance: "💰 Illegal Finance",
      proxy: "🔧 Unblock Service",
      spam_link: "🔗 Spam Link",
    },
  },
};

function detectLang(from) {
  const code = (from && from.language_code) || "";
  return code.startsWith("zh") ? "zh" : "en";
}

// 读用户语言偏好（存 GitHub user_prefs/<id>.json），无则返回 null 走自动检测
async function getUserLang(env, fromId) {
  if (!fromId) return null;
  const f = await ghGet(env, `user_prefs/${fromId}.json`);
  if (f) {
    try {
      const d = JSON.parse(b64decode(f.content));
      if (d.lang && LANGS[d.lang]) return d.lang;
    } catch (e) { /* ignore corrupt prefs */ }
  }
  return null;
}

async function setUserLang(env, fromId, lang) {
  const path = `user_prefs/${fromId}.json`;
  const existing = await ghGet(env, path);
  const data = { lang, updated_at: new Date().toISOString() };
  await ghPut(env, path, JSON.stringify(data, null, 2), existing ? existing.sha : null, `prefs: ${fromId} -> ${lang}`);
}

async function resolveLang(env, from) {
  const fromId = from ? from.id : null;
  const pref = await getUserLang(env, fromId);
  return pref || detectLang(from);
}

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
  const text = (msg.text || msg.caption || "").trim();
  const fromId = msg.from ? msg.from.id : null;
  const lower = text.toLowerCase();

  // ---- 命令处理（/start /help /lang）----
  if (lower.startsWith("/start") || lower.startsWith("/help") || lower.startsWith("/lang")) {
    if (!fromId) return;
    const lang = await resolveLang(env, msg.from);
    const t = LANGS[lang];

    if (lower.startsWith("/lang")) {
      const kb = [
        [{ text: "🇨🇳 中文", callback_data: "lang:zh" }],
        [{ text: "🇬🇧 English", callback_data: "lang:en" }],
      ];
      await tgApi(env.BOT_TOKEN, "sendMessage", {
        chat_id: fromId,
        text: t.langPrompt,
        reply_markup: { inline_keyboard: kb },
      });
    } else if (lower.startsWith("/start")) {
      await tgApi(env.BOT_TOKEN, "sendMessage", { chat_id: fromId, text: t.start });
    } else {
      await tgApi(env.BOT_TOKEN, "sendMessage", { chat_id: fromId, text: t.help });
    }
    return;
  }

  if (text.length < 10) return; // 太短无法提取特征，忽略
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
    const lang = await resolveLang(env, msg.from);
    const t = LANGS[lang];
    const kb = chunk(
      CATEGORIES.map((c) => ({
        text: t.categories[c] || c,
        callback_data: `cat:${date}:${hash}:${c}`,
      })),
      3
    );
    kb.push([{ text: t.skip, callback_data: `cat:${date}:${hash}:__skip__` }]);
    await tgApi(env.BOT_TOKEN, "sendMessage", {
      chat_id: fromId,
      text: t.received,
      reply_markup: { inline_keyboard: kb },
    });
  }
}

async function handleCallback(env, cb) {
  const chatId = cb.message && cb.message.chat ? cb.message.chat.id : null;
  const msgId = cb.message ? cb.message.message_id : null;

  // 语言切换回调：lang:zh / lang:en
  const lm = (cb.data || "").match(/^lang:(zh|en)$/);
  if (lm) {
    const lang = lm[1];
    if (cb.from && cb.from.id) {
      await setUserLang(env, cb.from.id, lang);
    }
    const t = LANGS[lang];
    await tgApi(env.BOT_TOKEN, "answerCallbackQuery", {
      callback_query_id: cb.id,
      text: t.langSet,
    });
    // 收缩语言选择菜单，显示确认
    if (chatId && msgId) {
      await tgApi(env.BOT_TOKEN, "editMessageText", {
        chat_id: chatId,
        message_id: msgId,
        text: t.langChosen,
      });
    }
    return;
  }

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
  const lang = await resolveLang(env, cb.from);
  const t = LANGS[lang];
  const label = cat === "__skip__" ? null : (t.categories[cat] || cat);
  await tgApi(env.BOT_TOKEN, "answerCallbackQuery", {
    callback_query_id: cb.id,
    text: cat === "__skip__" ? t.skipped : t.marked(label),
  });
  // 收缩类别选择菜单，显示确认（按钮消失）
  if (chatId && msgId) {
    await tgApi(env.BOT_TOKEN, "editMessageText", {
      chat_id: chatId,
      message_id: msgId,
      text: cat === "__skip__" ? t.skippedChosen : t.categoryChosen(label),
    });
  }
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
