#!/usr/bin/env python3
"""Import Nagram's community regex list into this repo.

Nagram (https://ddgksf2013.top/nagram/) ships PCRE-style rules that use the
inline (?i) flag, which Dart's RegExp cannot parse. This script normalises
them to our grammar and writes external/nagram.json, which build.py merges
into the flat rules.json the App pulls.

Usage:
    # 1) Paste Nagram's rules (one per line) into nagram_raw.txt, then:
    python3 nagram_sync.py
    # 2) Or attempt a live fetch (the site is JS-rendered, so this usually
    #    returns HTML and falls back to nagram_raw.txt):
    python3 nagram_sync.py --fetch

After import, run `python3 build.py` to regenerate rules.json.
"""

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "nagram_raw.txt"
OUT = ROOT / "external" / "nagram.json"
CATS = ROOT / "categories.json"
SOURCE_URL = "https://ddgksf2013.top/nagram/"


def normalize(line: str) -> dict | None:
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("//"):
        return None
    case = False
    pat = s
    if pat.startswith("(?i)"):
        case = False
        pat = pat[4:]
    elif pat.startswith("(?-i)"):
        case = True
        pat = pat[5:]
    pat = pat.replace("(?i:", "(?:").replace("(?-i:", "(?:")
    if pat.startswith("/") and pat.count("/") >= 2:
        last = pat.rfind("/")
        p, flags = pat[1:last], pat[last + 1:]
        case = "i" not in flags
        pat = p
    pat = pat.strip()
    if not pat:
        return None
    return {"kind": "regex", "pattern": pat, "caseSensitive": case,
            "source": "nagram"}


def main() -> int:
    fetch = "--fetch" in sys.argv
    lines: list[str] = []
    if fetch:
        try:
            req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "mithkaX-rules"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            # Best-effort: pull lines that look like regex rules out of the page.
            for cand in html.splitlines():
                cand = cand.strip().strip("\"',")
                if cand.startswith("(?i)") or (cand.startswith("/") and "re:" in cand):
                    lines.append(cand)
            print(f"fetched {len(lines)} candidate lines from live site")
        except Exception as exc:  # noqa: BLE001
            print(f"live fetch failed ({exc}); falling back to {RAW.name}", file=sys.stderr)
    if not lines and RAW.exists():
        lines = RAW.read_text(encoding="utf-8").splitlines()
    rules = [r for r in (normalize(l) for l in lines) if r]
    OUT.write_text(json.dumps(
        {"format": "mithkaX-adfilter/1", "source": "nagram",
         "updatedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "rules": rules}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(rules)} nagram rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
