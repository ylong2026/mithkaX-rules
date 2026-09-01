# -*- coding: utf-8 -*-
"""机器人共享工具：路径、归一化、分类、去重、git 推送。

复用仓库根目录 build.py 的 parse_line / normalize_regex，保证解析逻辑单一事实源。
"""
import sys
import re
import json
import hashlib
import unicodedata
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import build  # noqa: E402  （parse_line / normalize_regex 来源）

PENDING_DIR = REPO_ROOT / "pending"
CANDIDATES_DIR = REPO_ROOT / "candidates"
POISON_DIR = REPO_ROOT / "poison"
# 低信任通道：非转发（粘贴）进来，需 Owner 复核才计入公开规则
UNVERIFIED_DIR = REPO_ROOT / "unverified"
# 申诉通道：用户认为被误拦的消息
APPEALS_DIR = REPO_ROOT / "appeals"
# 新类别提案：后台检测到不属于现有类的簇，需 Owner 审批才进 categories.json
PROPOSALS_DIR = REPO_ROOT / "category_proposals"
INBOX_FILE = REPO_ROOT / "inbox_rules.json"
CATS_FILE = REPO_ROOT / "categories.json"
RULES_FILE = REPO_ROOT / "rules.json"


def ensure_dirs():
    for d in (PENDING_DIR, CANDIDATES_DIR, POISON_DIR,
              UNVERIFIED_DIR, APPEALS_DIR, PROPOSALS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------- 类别元信息 ----------------
def load_category_ids():
    """返回 [(id, label), ...]，供机器人弹类别选择按钮 / 校验用。"""
    data = load_categories()
    return [(c["id"], c.get("label", c["id"])) for c in data.get("categories", [])]


def add_category(cid, label):
    """Owner 审批新类别：追加到 categories.json（无规则），返回是否新增。"""
    data = load_categories()
    if any(c["id"] == cid for c in data.get("categories", [])):
        return False
    data.setdefault("categories", []).append({"id": cid, "label": label, "rules": []})
    CATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# ---------------- 机器人运行状态（信誉层）----------------
def bot_state_path():
    return REPO_ROOT / "bot" / "state.json"


def load_bot_state():
    p = bot_state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"users": {}, "global_today": {"date": "", "count": 0}}


def save_bot_state(s):
    bot_state_path().write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def normalize(text: str) -> str:
    """归一化：全角->半角、去零宽、小写、压扁空白、去 URL/@mention/长数字噪声。"""
    out = unicodedata.normalize("NFKC", text)
    out = "".join(ch for ch in out if not unicodedata.combining(ch) and ord(ch) > 31)
    out = out.lower()
    out = re.sub(r"https?://\S+", " ", out)
    out = re.sub(r"t\.me/\S+", " ", out)
    out = re.sub(r"@\w+", " ", out)
    out = re.sub(r"\d+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def load_categories() -> dict:
    return json.loads(CATS_FILE.read_text(encoding="utf-8"))


def _compiled_category_rules():
    cache = getattr(load_categories, "_compiled", None)
    if cache is not None:
        return cache
    compiled = []
    for cat in load_categories().get("categories", []):
        for line in cat.get("rules", []):
            r = build.parse_line(line)
            if r and r["kind"] == "regex":
                try:
                    rx = re.compile(build.normalize_regex(r["pattern"]), re.I)
                    compiled.append((cat["id"], rx))
                except re.error:
                    pass
    load_categories._compiled = compiled
    return compiled


def classify(text: str):
    """用现有规则测试原始消息，返回命中的类别 id 列表。"""
    hits = []
    for cid, rx in _compiled_category_rules():
        if rx.search(text):
            hits.append(cid)
    return hits


def load_existing_rules() -> list:
    if not RULES_FILE.exists():
        return []
    return json.loads(RULES_FILE.read_text(encoding="utf-8")).get("rules", [])


def is_covered(text: str) -> bool:
    """该消息是否已被现有 rules.json 覆盖（命中即视为重复，无需再生成）。"""
    for rule in load_existing_rules():
        pat = rule.get("pattern", "")
        kind = rule.get("kind")
        if kind == "regex":
            try:
                if re.compile(build.normalize_regex(pat), re.I).search(text):
                    return True
            except re.error:
                pass
        elif kind == "keyword" and pat.lower() in text.lower():
            return True
        elif kind == "domain" and pat.lower() in text.lower():
            return True
    return False


def safe_compile(pattern: str):
    try:
        return re.compile(build.normalize_regex(pattern), re.I)
    except re.error:
        return None


def rule_dedup_key(rule: dict) -> str:
    return f"{'a' if rule.get('allow') else 'b'}:{rule['kind']}:" \
           f"{'s' if rule.get('caseSensitive') else 'i'}:{rule['pattern'].lower()}"


def git_commit_push(message: str):
    """在仓库本地克隆里提交并推送（远端 URL 已含 PAT）。"""
    for c in (["git", "add", "-A"], ["git", "commit", "-q", "-m", message], ["git", "push"]):
        subprocess.run(c, cwd=str(REPO_ROOT), check=False)
