#!/usr/bin/env python3
"""Merge categories.json + external/*.json into the flat rules.json the App pulls.

Run:  python3 build.py
The App subscribes to the raw URL of rules.json only. categories.json is the
human-editable source of truth; external files hold imported/community lists.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def normalize_regex(pat: str) -> str:
    """Drop PCRE inline flags Dart RegExp cannot parse."""
    if pat.startswith("(?i)"):
        pat = pat[4:]
    elif pat.startswith("(?-i)"):
        pat = pat[5:]
    return pat.replace("(?i:", "(?:").replace("(?-i:", "(?:")


def parse_line(line: str) -> dict | None:
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("//"):
        return None
    allow = False
    low = s.lower()
    if low.startswith("allow:"):
        allow = True
        s = s[len("allow:"):].strip()
        low = s.lower()
    if low.startswith("domain:"):
        host = s[len("domain:"):].strip()
        return {"kind": "domain", "pattern": host, "allow": allow} if host else None
    if low.startswith("sender:"):
        sid = s[len("sender:"):].strip()
        return {"kind": "sender", "pattern": sid, "allow": allow} if sid.isdigit() else None
    if low.startswith("re:") or low.startswith("regex:"):
        pat = s[s.index(":") + 1:].strip()
        if not pat:
            return None
        return {"kind": "regex", "pattern": normalize_regex(pat), "allow": allow}
    if len(s) >= 2 and s.startswith("/"):
        last = s.rfind("/")
        if last > 0:
            pat = s[1:last]
            flags = s[last + 1:]
            if pat:
                return {"kind": "regex", "pattern": normalize_regex(pat),
                        "caseSensitive": "i" not in flags, "allow": allow}
    return {"kind": "keyword", "pattern": s, "allow": allow}


def parse_object(obj: dict) -> dict | None:
    kind = obj.get("kind")
    pat = obj.get("pattern")
    if kind not in ("keyword", "regex", "domain", "sender") or not isinstance(pat, str):
        return None
    pat = pat.strip()
    if not pat:
        return None
    if kind == "sender" and not pat.isdigit():
        return None
    if kind == "regex":
        pat = normalize_regex(pat)
    return {"kind": kind, "pattern": pat,
            "caseSensitive": bool(obj.get("caseSensitive")),
            "allow": bool(obj.get("allow")),
            "source": obj.get("source")}


def collect_from_payload(payload, source: str, out: list, seen: set):
    items = payload.get("rules") or payload.get("ads") or payload.get("items")
    if isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        return
    for entry in items:
        rule = parse_line(entry) if isinstance(entry, str) else parse_object(entry) if isinstance(entry, dict) else None
        if rule:
            add(rule, source, out, seen)
    allow_list = payload.get("allow") if isinstance(payload, dict) else None
    if isinstance(allow_list, list):
        for entry in allow_list:
            rule = parse_line(entry) if isinstance(entry, str) else parse_object(entry) if isinstance(entry, dict) else None
            if rule:
                rule["allow"] = True
                add(rule, source, out, seen)


def add(rule: dict, source: str, out: list, seen: set):
    rule.setdefault("source", source)
    key = f"{'a' if rule.get('allow') else 'b'}:{rule['kind']}:" \
          f"{'s' if rule.get('caseSensitive') else 'i'}:{rule['pattern'].lower()}"
    if key in seen:
        return
    seen.add(key)
    out.append(rule)


def main() -> int:
    cats = json.loads((ROOT / "categories.json").read_text(encoding="utf-8"))
    out: list[dict] = []
    seen: set[str] = set()

    for cat in cats.get("categories", []):
        for line in cat.get("rules", []):
            rule = parse_line(line)
            if rule:
                rule["source"] = f"cat:{cat['id']}"
                add(rule, f"cat:{cat['id']}", out, seen)

    for line in cats.get("allow", []):
        rule = parse_line(line)
        if rule:
            rule["allow"] = True
            rule["source"] = "allow"
            add(rule, "allow", out, seen)

    for ext in cats.get("external", []):
        if not ext.get("enabled"):
            continue
        path = ROOT / ext["file"]
        if not path.exists():
            print(f"skip external (missing file): {ext['file']}", file=sys.stderr)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        collect_from_payload(data, ext.get("id", "external"), out, seen)

    version = int(datetime.now(timezone.utc).timestamp())
    result = {
        "format": "mithkaX-adfilter/1",
        "version": version,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ruleCount": len(out),
        "rules": out,
    }
    (ROOT / "rules.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote rules.json: {len(out)} rules, version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
