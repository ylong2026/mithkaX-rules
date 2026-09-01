# -*- coding: utf-8 -*-
"""Owner 后端：定时处理 pending/ 队列，生成候选规则。

流程（与 DEFENSE.md 一致）：
1. 读取 pending/ 全部提交，按 hash 去重（保留 reporter_ids 计数）。
2. 已被现有 rules.json 覆盖 -> 标记 duplicate，跳过。
3. 词典提取稳定特征 -> 生成模板正则；分不出类且 AI 开启 -> 调 AI。
4. 多独立举报同签名（>=AUTO_CANDIDATE_MIN_REPORTERS）-> 直接成候选（高置信）。
5. 类无法归入现有类别 -> 写 category_proposals/ 提案，需 Owner 审批。
6. 沙箱自测：必须命中样本、且不得误伤正常样本，否则标记 flagged。
7. 候选带 provenance（reporters 计数 / evidence 样本），写入 candidates/ + review md。

本脚本用 Owner 自己的 AI Key（公众转发不触发）。可放 cron：0 */4 * * *。
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import common
try:
    import ai_review
except ImportError:
    ai_review = None
try:
    from config import AUTO_CANDIDATE_MIN_REPORTERS
except ImportError:
    from config_defaults import AUTO_CANDIDATE_MIN_REPORTERS

STOP = set("的 了 是 我 你 他 她 在 有 和 就 不 也 都 把 被 给 这 那 啊 吗 呢 吧 哦 嗯 请 加 微信 群 个 们 会 能 要 去 到 用 上 下 中 里 后 前 对 从 向 以 为 与 及 或 等".split())

THEME_HINTS = {
    "airport": ["机场", "节点", "vpn", "翻墙", "解锁", "netflix", "disney", "hbo"],
    "gambling": ["棋牌", "博彩", "赌博", "开元", "百家乐", "赌球"],
    "adult": ["约炮", "裸聊", "成人", "色情", "卖片", "原味"],
    "phishing_scam": ["刷单", "返利", "兼职", "杀猪盘", "解冻", "民族资产"],
    "selling": ["招代理", "代发", "免费领", "扫码领", "微商", "代购"],
    "finance_illegal": ["黑户", "征信", "套现", "洗白", "贷款", "信用卡"],
    "proxy_service": ["代办", "代充", "代解封", "代考", "解封", "解冻"],
    "spam_link": ["加群", "邀请码", "内部群", "推广", "拉人", "二维码"],
}

BENIGN = ["你好在吗", "今天天气不错", "晚上一起吃个饭", "这个文档你看了吗",
          "周末有空一起爬山", "谢谢你的帮助", "请问这个怎么用", "收到 thanks"]


def collect_pending():
    subs = []
    if not common.PENDING_DIR.exists():
        return subs
    for f in common.PENDING_DIR.rglob("*.json"):
        try:
            subs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return subs


def distinctive_tokens(norm):
    toks = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", norm)
    cnt = Counter(t for t in toks if t not in STOP)
    return [t for t, _ in cnt.most_common(3)]


def guess_theme(norm):
    for cid, words in THEME_HINTS.items():
        if any(w in norm for w in words):
            return cid
    return None


def make_rule(norm, theme):
    toks = distinctive_tokens(norm)
    if theme and THEME_HINTS.get(theme):
        seeds = [w for w in THEME_HINTS[theme] if w in norm][:2]
        toks = (seeds + toks)[:3]
    if not toks:
        return None
    return "re:(?:" + "|".join(re.escape(t) for t in toks) + ")"


def self_test(pat, text):
    rx = common.safe_compile(pat)
    if not rx:
        return False, "编译失败"
    if not rx.search(text):
        return False, "未命中样本"
    for b in BENIGN:
        if rx.search(b):
            return False, f"误伤正常样本：{b}"
    return True, "ok"


def write_proposal(suggested_id, label, sample):
    """类无法归入现有类别 -> 写提案，需 Owner 审批才进 categories.json。"""
    common.PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    h = common.content_hash(sample)[:12]
    f = common.PROPOSALS_DIR / f"{suggested_id}_{h}.json"
    f.write_text(json.dumps({
        "suggested_id": suggested_id, "label": label,
        "sample": sample[:120], "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def run(use_ai=True):
    common.ensure_dirs()
    subs = collect_pending()
    if not subs:
        print("pending 为空，无需处理。")
        return

    existing_ids = {cid for cid, _ in common.load_category_ids()}
    seen_keys = set(common.rule_dedup_key(r) for r in common.load_existing_rules())
    candidates = []
    proposals = []
    for sub in subs:
        text = sub.get("text", "")
        reporter_ids = set(sub.get("reporter_ids", [sub.get("from_id")]))
        reporter_count = len(reporter_ids) if reporter_ids else sub.get("reports", 1)

        if common.is_covered(text):
            sub["status"] = "duplicate"
            continue
        norm = sub.get("normalized") or common.normalize(text)
        theme = guess_theme(norm)
        pat = make_rule(norm, theme)
        method = "dict"
        proposed_this = False
        if not pat and use_ai and ai_review:
            ai = ai_review.review(text, theme)
            if ai and ai.get("regex"):
                pat = ai["regex"] if ai["regex"].startswith("re:") else "re:" + ai["regex"]
                method = "ai"
                ai_cat = ai.get("category")
                if ai_cat and ai_cat not in existing_ids:
                    # AI 建议了新类别 -> 提案（规则仍按未分类入库，类由 Owner 决定）
                    proposals.append((ai_cat, ai.get("label", ai_cat), text))
                    theme = "unclassified"
                    proposed_this = True
                else:
                    theme = ai_cat or theme

        # 多独立举报同签名：高置信，直接成候选（即便词典/AI 拿不出好正则）
        if not pat and reporter_count >= AUTO_CANDIDATE_MIN_REPORTERS:
            toks = distinctive_tokens(norm)
            if toks:
                pat = "re:(?:" + "|".join(re.escape(t) for t in toks) + ")"
                method = "multi-report"

        unclassified = (theme is None or theme == "unclassified")
        # 无法归入现有类别 -> 写新类别提案（无论是否生成出正则，都让 Owner 决定类目）
        if unclassified and not proposed_this:
            proposals.append((f"unclassified_{common.content_hash(text)[:6]}",
                              "未分类样本（需 Owner 命名）", text))

        if not pat:
            sub["status"] = "needs_review"
            candidates.append({"from_hash": sub["hash"], "category": "unclassified",
                               "method": "none", "status": "needs_review",
                               "reporters": reporter_count,
                               "evidence": text[:120],
                               "note": "词典与 AI 都无法生成，需人工看"})
            continue

        obj = common.build.parse_line(pat)
        if not obj:
            sub["status"] = "flagged"
            continue
        dk = common.rule_dedup_key(obj)
        if dk in seen_keys:
            sub["status"] = "duplicate"
            continue
        seen_keys.add(dk)
        # 注意：self_test 必须用解析后的 pattern（去掉 re: 前缀），否则永远匹配不到样本
        ok, why = self_test(obj["pattern"], text)
        entry = {"rule": obj, "from_hash": sub["hash"],
                 "category": theme or "unclassified", "method": method,
                 "reporters": reporter_count, "evidence": text[:120],
                 "sample": text[:80]}
        if ok:
            entry["status"] = "candidate"
            sub["status"] = "candidate"
        else:
            entry["status"] = "flagged"
            entry["note"] = why
            sub["status"] = "flagged"
        candidates.append(entry)

    # 写候选
    common.CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    (common.CANDIDATES_DIR / "candidate_rules.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 写新类别提案
    for suggested_id, label, sample in proposals:
        write_proposal(suggested_id, label, sample)

    # 写人读复核单
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# 候选规则复核单 {date}\n", f"共 {len(candidates)} 条\n"]
    for i, c in enumerate(candidates, 1):
        if c["status"] == "needs_review":
            lines.append(f"{i}. ⚠️ needs_review | 类别:{c.get('category')} | 举报人数:{c.get('reporters')} | {c.get('note')}\n")
            continue
        r = c["rule"]
        lines.append(f"{i}. [{c['status']}] {c['method']} | 类别:{c.get('category')} | 举报:{c.get('reporters')} | "
                     f"{r['kind']}:{r['pattern']}\n   样本: {c.get('sample','')}\n")
    if proposals:
        lines.append(f"\n# 新类别提案 {len(proposals)} 条（Owner 审批后用 /addcat 加入）\n")
        for suggested_id, label, sample in proposals:
            lines.append(f"- <code>{suggested_id}</code> {label} | 样本: {sample[:40]}\n")
    (common.CANDIDATES_DIR / f"review_{date}.md").write_text("".join(lines), encoding="utf-8")

    n_cand = sum(1 for c in candidates if c["status"] == "candidate")
    n_flag = sum(1 for c in candidates if c["status"] == "flagged")
    n_dup = sum(1 for c in candidates if c["status"] == "needs_review")
    print(f"处理完成：候选 {n_cand} / 标记 {n_flag} / 待人工 {n_dup} / 新类提案 {len(proposals)}。见 candidates/")


if __name__ == "__main__":
    run(use_ai=True)
