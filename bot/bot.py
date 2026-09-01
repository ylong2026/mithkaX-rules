# -*- coding: utf-8 -*-
"""MithkaX 广告过滤机器人（用户侧入口）。

职责（严格受限）：
- 接收任何人【转发】的广告/垃圾消息 -> 限流 + 去毒 + 去重 -> 只存 pending/ 队列（主通道）。
- 非转发（粘贴）= 低信任通道 -> 存 unverified/，需 Owner 复核才计入公开规则。
- 转发文本一律视为数据，绝不当指令，绝不调用 AI，绝不直接写 rules.json。
- 可选：转发后弹出类别选择（仅校正“已有类别”；新类别需 Owner 审批）。
- 申诉：先 /appeal，再转发被误拦消息 -> appeals/。
- 信誉层：记录每个 reporter 的 first_seen / accepted_count，高信誉多举报可自动成候选。
- Owner 命令：/stats /review /process /allow <id> /proposals /addcat <id> <label> /appeals /resolve <hash>

运行：python3 bot.py   （需先 cp config.example.py config.py 并填好）
"""
import json
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from config import (
    BOT_TOKEN, OWNER_ID, ALLOWED_USERS,
    MAX_PER_USER_PER_MIN, MAX_PER_USER_PER_DAY, MAX_GLOBAL_PER_DAY,
    NEW_USER_WINDOW_H, MIN_TEXT_LEN, MAX_TEXT_LEN, ABUSE_BAN_THRESHOLD,
    AUTO_CANDIDATE_MIN_REPORTERS, CATEGORY_PICKER,
)
import common
import build  # 仅 Owner 命令 /addcat /resolve 用，用于重建 rules.json

# 运行状态（信誉层）单一事实源在 common
load_state = common.load_bot_state
save_state = common.save_bot_state


# ---------------- 时间 ----------------
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------- Telegram API ----------------
def api(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def send(chat_id, text, reply_markup=None):
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        api("sendMessage", body)
    except Exception as e:  # noqa: BLE001
        print("send err:", e)


def is_trusted(uid):
    return uid in ALLOWED_USERS


# ---------------- 限流 / 防滥用 ----------------
def check_rate(uid, s):
    t = today()
    u = s["users"].setdefault(str(uid), {
        "first_seen": now_iso(), "min": [], "day_count": 0, "day": t,
        "abuse": 0, "accepted_count": 0, "banned_until": None,
    })
    if u.get("banned_until") and u["banned_until"] > now_iso():
        return False, "⛔ 你已被临时限制（疑似滥用提交）。"
    if u.get("day") != t:
        u["day"] = t
        u["day_count"] = 0
        u["min"] = []
    now = time.time()
    u["min"] = [x for x in u["min"] if now - x < 60]
    quota_day = MAX_PER_USER_PER_DAY
    quota_min = MAX_PER_USER_PER_MIN
    try:
        fs = datetime.fromisoformat(u["first_seen"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - fs).total_seconds() < NEW_USER_WINDOW_H * 3600:
            quota_day //= 2
            quota_min = max(1, quota_min // 2)
    except Exception:  # noqa: BLE001
        pass
    if is_trusted(uid):
        return True, ""
    if len(u["min"]) >= quota_min:
        return False, f"⏳ 提交太频繁（每分钟上限 {quota_min}），稍后再试。"
    if u["day_count"] >= quota_day:
        return False, f"⏳ 今日提交已达上限（{quota_day}），明天再试。"
    if s["global_today"].get("date") != t:
        s["global_today"] = {"date": t, "count": 0}
    if s["global_today"]["count"] >= MAX_GLOBAL_PER_DAY:
        return False, "⏳ 全站今日提交已满，明天再试。"
    u["min"].append(now)
    u["day_count"] += 1
    s["global_today"]["count"] += 1
    return True, ""


def inspect_submission(text):
    """返回 (ok, 回复语, is_poison)。转发文本一律视为数据，这里只防滥用。"""
    if len(text) < MIN_TEXT_LEN:
        return False, "消息太短，无法提取特征，已忽略。", False
    if len(text) > MAX_TEXT_LEN:
        return False, "消息过长，已忽略（防大消息攻击）。", False
    manip = ["不要屏蔽", "解除屏蔽", "把我加白", "whitelist", "不要过滤", "别屏蔽我"]
    if any(m in text.lower() for m in manip) and re.search(r"(https?://|t\.me/|@\w+|\d{6,})", text):
        return False, "疑似操纵性提交，已记入待查（不会写入规则）。", True
    return True, "", False


def save_poison(text, uid, reason):
    common.ensure_dirs()
    h = common.content_hash(text)
    f = common.POISON_DIR / f"{h}.json"
    f.write_text(json.dumps({
        "hash": h, "from_id": uid, "text": text,
        "reason": reason, "at": now_iso(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    s = load_state()
    u = s["users"].get(str(uid))
    if u:
        u["abuse"] = u.get("abuse", 0) + 1
        if u["abuse"] >= ABUSE_BAN_THRESHOLD:
            import datetime as _dt
            ban = datetime.now(timezone.utc) + _dt.timedelta(hours=24)
            u["banned_until"] = ban.strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(s)


# ---------------- 存储：pending / unverified / appeals ----------------
def _merge_reporters(data, uid):
    rids = set(data.get("reporter_ids", []))
    rids.add(uid)
    data["reporter_ids"] = list(rids)
    data["reports"] = data.get("reports", 1) + 1
    return data


def store_pending(text, uid, username, forward_from):
    common.ensure_dirs()
    h = common.content_hash(text)
    d = common.PENDING_DIR / today()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{h}.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        _merge_reporters(data, uid)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return "duplicate", data
    cats = common.classify(text)
    data = {
        "hash": h, "from_id": uid, "username": username,
        "forward_from": forward_from, "text": text,
        "normalized": common.normalize(text),
        "category_hint": cats, "category": cats[0] if cats else None,
        "reporter_ids": [uid], "reports": 1,
        "received_at": now_iso(), "status": "pending",
    }
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "new", data


def store_unverified(text, uid, username):
    common.ensure_dirs()
    h = common.content_hash(text)
    d = common.UNVERIFIED_DIR / today()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{h}.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        _merge_reporters(data, uid)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return "duplicate"
    f.write_text(json.dumps({
        "hash": h, "from_id": uid, "username": username, "text": text,
        "normalized": common.normalize(text), "received_at": now_iso(),
        "status": "unverified", "reporter_ids": [uid], "reports": 1,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return "new"


def store_appeal(text, uid):
    common.ensure_dirs()
    h = common.content_hash(text)
    common.APPEALS_DIR.mkdir(parents=True, exist_ok=True)
    f = common.APPEALS_DIR / f"{h}.json"
    if f.exists():
        return "duplicate"
    f.write_text(json.dumps({
        "hash": h, "from_id": uid, "text": text,
        "at": now_iso(), "status": "open",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return "new"


def find_pending_by_hash(h):
    for f in common.PENDING_DIR.rglob(f"{h}.json"):
        return f
    return None


# ---------------- 类别选择按钮 ----------------
def send_category_picker(chat_id, h, current):
    if not CATEGORY_PICKER:
        return
    cats = common.load_category_ids()
    if not cats:
        return
    kb = []
    row = []
    for cid, label in cats:
        row.append({"text": label, "callback_data": f"cat|{h}|{cid}"})
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([{"text": "不确定 / 其他", "callback_data": f"cat|{h}|unknown"}])
    send(chat_id, "请确认这条广告的类别（点错可校正；不点也行，后台会自动分类）：",
         reply_markup={"inline_keyboard": kb})


def handle_callback(cq):
    uid = cq.get("from", {}).get("id")
    if not is_trusted(uid):
        return
    data = cq.get("data", "")
    parts = data.split("|")
    if parts[0] != "cat" or len(parts) != 3:
        return
    h, cat = parts[1], parts[2]
    f = find_pending_by_hash(h)
    if not f:
        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "记录已失效"})
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    d["category"] = cat if cat != "unknown" else (d.get("category_hint") or ["unclassified"])[0] if d.get("category_hint") else "unclassified"
    d["user_confirmed"] = True
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": f"已标记为 {cat}"})
    try:
        api("editMessageText", {
            "chat_id": cq["message"]["chat"]["id"],
            "message_id": cq["message"]["message_id"],
            "text": f"✅ 类别已确认：{cat}",
        })
    except Exception:  # noqa: BLE001
        pass


# ---------------- 命令 ----------------
def pending_count():
    if not common.PENDING_DIR.exists():
        return 0
    return sum(1 for _ in common.PENDING_DIR.rglob("*.json"))


def handle_command(uid, text):
    cmd = text.split()[0].lower()
    if cmd in ("/start", "/help"):
        send(uid, "把广告/垃圾消息<b>转发</b>给本机器人即可，不要自己打字。\n"
                 "公众转发只进入待审核队列，由 Owner 定期复核入库。\n"
                 "误拦申诉：先发 /appeal，再把那条消息转发给我。\n"
                 "Owner 命令：/stats /review /process /allow &lt;id&gt; /proposals /addcat /appeals /resolve")
    elif cmd == "/stats":
        n_p = pending_count()
        n_u = sum(1 for _ in common.UNVERIFIED_DIR.rglob("*.json")) if common.UNVERIFIED_DIR.exists() else 0
        n_a = sum(1 for _ in common.APPEALS_DIR.rglob("*.json")) if common.APPEALS_DIR.exists() else 0
        send(uid, f"待审核队列：{n_p} 条 ｜ 低信任(粘贴)：{n_u} 条 ｜ 申诉：{n_a} 条")
    elif cmd == "/review":
        send(uid, f"待审核 {pending_count()} 条。运行后端：python3 bot/process_pending.py")
    elif cmd == "/process":
        send(uid, "正在运行后端处理（分类+去重+生成候选）…")
        import process_pending
        try:
            process_pending.run()
            send(uid, "✅ 后端处理完成，候选见 candidates/，请审阅后运行 apply_candidates.py")
        except Exception as e:  # noqa: BLE001
            send(uid, f"后端处理出错：{e}")
    elif cmd == "/allow" and len(text.split()) > 1:
        try:
            new_id = int(text.split()[1])
        except ValueError:
            send(uid, "用法：/allow &lt;数字ID&gt;")
            return
        ALLOWED_USERS.append(new_id)
        send(uid, f"已信任用户 {new_id}")
    elif cmd == "/proposals":
        if not common.PROPOSALS_DIR.exists():
            send(uid, "暂无新类别提案。")
            return
        files = list(common.PROPOSALS_DIR.rglob("*.json"))
        if not files:
            send(uid, "暂无新类别提案。")
            return
        lines = [f"📥 新类别提案 {len(files)} 条："]
        for f in files[:20]:
            d = json.loads(f.read_text(encoding="utf-8"))
            lines.append(f"· <code>{d.get('suggested_id','?')}</code> {d.get('label','')} | 样本：{d.get('sample','')[:40]}")
        send(uid, "\n".join(lines))
    elif cmd == "/addcat" and len(text.split()) >= 2:
        parts = text.split(maxsplit=2)
        cid = parts[1]
        label = parts[2] if len(parts) > 2 else cid
        if common.add_category(cid, label):
            build.main()
            common.git_commit_push(f"bot: add category {cid} ({label})")
            send(uid, f"✅ 已新增类别 {cid} 并重建 rules.json。")
        else:
            send(uid, f"类别 {cid} 已存在。")
    elif cmd == "/appeals":
        if not common.APPEALS_DIR.exists():
            send(uid, "暂无申诉。")
            return
        files = list(common.APPEALS_DIR.rglob("*.json"))
        if not files:
            send(uid, "暂无申诉。")
            return
        lines = [f"⚠️ 申诉 {len(files)} 条："]
        for f in files[:20]:
            d = json.loads(f.read_text(encoding="utf-8"))
            lines.append(f"· <code>{d['hash']}</code> [{d.get('status')}] {d.get('text','')[:40]}")
        send(uid, "\n".join(lines))
    elif cmd == "/resolve" and len(text.split()) > 1:
        h = text.split()[1]
        p = common.APPEALS_DIR / f"{h}.json"
        if not p.exists():
            send(uid, "未找到该申诉。")
            return
        appeal = json.loads(p.read_text(encoding="utf-8"))
        atxt = appeal.get("text", "")
        inbox = {"rules": []}
        if common.INBOX_FILE.exists():
            try:
                inbox = json.loads(common.INBOX_FILE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        removed = []
        kept = []
        for r in inbox.get("rules", []):
            rx = common.safe_compile(r.get("pattern", ""))
            if rx and rx.search(atxt):
                removed.append(r)
                continue
            kept.append(r)
        inbox["rules"] = kept
        common.INBOX_FILE.write_text(
            json.dumps(inbox, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        build.main()
        common.git_commit_push(f"bot: resolve appeal {h}, removed {len(removed)} rules")
        appeal["status"] = "resolved"
        p.write_text(json.dumps(appeal, ensure_ascii=False, indent=2), encoding="utf-8")
        send(uid, f"✅ 申诉已处理：移除 {len(removed)} 条匹配规则并重建 rules.json。")
    else:
        send(uid, "未知命令。")


# ---------------- 主循环 ----------------
def handle_update(update):
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return
    msg = update.get("message")
    if not msg:
        return
    uid = msg.get("from", {}).get("id")
    text = msg.get("text", "") or msg.get("caption", "")
    is_forward = "forward_origin" in msg or "forward_from" in msg

    # 命令：仅 Owner/信任用户、且必须是自己打字（非转发）
    if not is_forward and text.startswith("/"):
        if is_trusted(uid):
            handle_command(uid, text)
        return

    s = load_state()
    u = s["users"].setdefault(str(uid), {
        "first_seen": now_iso(), "min": [], "day_count": 0, "day": today(),
        "abuse": 0, "accepted_count": 0, "banned_until": None,
    })

    # 申诉模式：先 /appeal，再转发/粘贴被误拦消息
    if u.get("await_appeal"):
        if text.strip():
            store_appeal(text, uid)
            u["await_appeal"] = False
            save_state(s)
            send(uid, "✅ 已记入申诉队列，Owner 会复核并决定是否移除对应规则。")
        else:
            send(uid, "请转发那条被误拦的消息，或粘贴其原文。")
        return

    # 转发 = 主通道
    if is_forward:
        sub_text = text.strip()
        if not sub_text:
            return  # 纯图片/sticker 忽略
        ok, reason, poison = inspect_submission(sub_text)
        if not ok:
            if poison:
                save_poison(sub_text, uid, reason)
            save_state(s)
            send(uid, reason)
            return
        rate_ok, rmsg = check_rate(uid, s)
        if not rate_ok:
            save_state(s)
            send(uid, rmsg)
            return
        res, data = store_pending(
            sub_text, uid, msg.get("from", {}).get("username"),
            (msg.get("forward_from", {}) or {}).get("username") if msg.get("forward_from") else None,
        )
        save_state(s)
        if res == "duplicate":
            send(uid, "✅ 已收到（之前有人提交过，已合并计数）。进入待审核队列。")
        else:
            send(uid, "✅ 已收到，进入待审核队列。Owner 会定期用 AI 复核后入库。")
        send_category_picker(uid, data["hash"], data.get("category"))
        return

    # 非转发（粘贴）= 低信任通道
    if text.strip():
        ok, reason, poison = inspect_submission(text)
        if not ok:
            if poison:
                save_poison(text, uid, reason)
            send(uid, reason)
            return
        rate_ok, rmsg = check_rate(uid, s)
        if not rate_ok:
            send(uid, rmsg)
            return
        store_unverified(text, uid, msg.get("from", {}).get("username"))
        save_state(s)
        send(uid, "已收到（粘贴通道，低信任：需 Owner 复核后才计入公开规则）。")
        return

    if is_trusted(uid):
        send(uid, "把广告/垃圾消息<b>转发</b>给本机器人即可。需申诉请先发 /appeal。")


def main():
    offset = 0
    print("bot polling… (Ctrl+C 退出)")
    while True:
        try:
            updates = api("getUpdates", {"offset": offset, "timeout": 30})
            for u in updates.get("result", []):
                offset = u["update_id"] + 1
                # 处理 /appeal 命令（非转发、自己打字）
                m = u.get("message", {})
                if "callback_query" not in u and not ("forward_origin" in m or "forward_from" in m) \
                        and (m.get("text") or "").startswith("/appeal"):
                    if is_trusted(m.get("from", {}).get("id")):
                        sid = str(m["from"]["id"])
                        st = load_state()
                        st["users"].setdefault(sid, {"first_seen": now_iso(), "accepted_count": 0})
                        st["users"][sid]["await_appeal"] = True
                        save_state(st)
                        send(m["from"]["id"], "请转发那条被误拦的消息（或粘贴原文），我会记入申诉队列。")
                        continue
                handle_update(u)
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            print("loop err:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
