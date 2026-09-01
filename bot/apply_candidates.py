# -*- coding: utf-8 -*-
"""Owner 复核后入库：把候选合并进 inbox_rules.json，重新生成 rules.json 并提交。

用法：
  python3 bot/apply_candidates.py            # 预览（dry-run），不写文件
  python3 bot/apply_candidates.py --apply    # 写入 inbox_rules.json + build + git 提交推送
  python3 bot/apply_candidates.py --apply --only candidate,flagged   # 只接纳指定状态

只接纳 status 为 candidate / flagged 的；needs_review 永远需人工决定。
每条入库规则附 provenance（category / evidence / reporters / firstSeen），并累加各 reporter 的
accepted_count 信誉分（不把用户 ID 写入公开 rules.json，只存独立举报人数）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import common
import build


def load_candidates():
    p = common.CANDIDATES_DIR / "candidate_rules.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def find_pending_reporters(h):
    for f in common.PENDING_DIR.rglob(f"{h}.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return d.get("reporter_ids", [d.get("from_id")])
        except Exception:  # noqa: BLE001
            return []
    return []


def bump_reputation(reporter_ids):
    if not reporter_ids:
        return
    s = common.load_bot_state()
    for rid in set(reporter_ids):
        u = s["users"].setdefault(str(rid), {
            "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "accepted_count": 0,
        })
        u["accepted_count"] = u.get("accepted_count", 0) + 1
    common.save_bot_state(s)


def run(apply=False, only=None):
    cands = load_candidates()
    if not cands:
        print("无候选可入库。")
        return
    accept = set(only or ["candidate", "flagged"])
    to_add = [c for c in cands if c.get("status") in accept and c.get("rule")]

    if not apply:
        print(f"[dry-run] 将入库 {len(to_add)} 条：")
        for c in to_add:
            r = c["rule"]
            print(f"  {r['kind']}:{r['pattern']}  (类别:{c.get('category')}, {c['method']}, 举报:{c.get('reporters')})")
        return

    # 合并进 inbox_rules.json
    inbox = {"rules": []}
    if common.INBOX_FILE.exists():
        try:
            inbox = json.loads(common.INBOX_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    seen = set(common.rule_dedup_key(r) for r in inbox.get("rules", []))
    added = 0
    cats_summary = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in to_add:
        obj = c["rule"]
        # 证据化 provenance（不泄露用户 ID，仅存独立举报人数）
        obj["category"] = c.get("category") or "unclassified"
        if c.get("evidence"):
            obj["evidence"] = c["evidence"]
        if c.get("reporters"):
            obj["reporters"] = c["reporters"]
        obj["firstSeen"] = now
        obj.setdefault("source", f"inbox:{obj['category']}")
        if common.rule_dedup_key(obj) in seen:
            continue
        seen.add(common.rule_dedup_key(obj))
        inbox["rules"].append(obj)
        added += 1
        cats_summary[obj["category"]] = cats_summary.get(obj["category"], 0) + 1
        # 累加信誉：反查该候选来源的多位 reporter
        bump_reputation(find_pending_reporters(c.get("from_hash", "")))

    common.INBOX_FILE.write_text(
        json.dumps(inbox, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 重新生成 rules.json
    build.main()
    cat_str = ", ".join(f"{k}×{v}" for k, v in sorted(cats_summary.items()))
    common.git_commit_push(
        f"bot: apply {added} candidate rules ({cat_str}) from submissions")
    print(f"✅ 已入库 {added} 条（{cat_str}）并提交推送。")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    only = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = sys.argv[idx + 1].split(",")
    run(apply=apply, only=only)
