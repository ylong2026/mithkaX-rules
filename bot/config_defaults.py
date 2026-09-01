# -*- coding: utf-8 -*-
"""CI / 无 config.py 时的安全默认值。

设计：config.py（gitignored，含真实 BOT_TOKEN）若存在则优先被脚本读取；
若不存在（GitHub Actions / 干净 checkout），脚本 fallback 到本文件。
本文件不含任何秘密，可安全提交。
"""
import os

# ---- Telegram 机器人 ----
BOT_TOKEN = os.environ.get("ADF_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("ADF_OWNER_ID", "0"))
ALLOWED_USERS = [OWNER_ID]

# ---- GitHub（仅 Owner 后端写规则用）----
GITHUB_REPO = "ylong2026/mithkaX-rules"
GITHUB_BRANCH = "main"

# ---- AI（默认关闭；无 Key 则不调用，公众转发绝不触发）----
AI_ENABLED = False
AI_BASE_URL = os.environ.get("ADF_AI_BASE_URL", "https://api.deepseek.com/v1")
AI_API_KEY = os.environ.get("ADF_AI_KEY", "")
AI_MODEL = os.environ.get("ADF_AI_MODEL", "deepseek-chat")

# ---- 限流 / 防滥用 ----
MAX_PER_USER_PER_MIN = 2        # 单用户每分钟最多提交条数
MAX_PER_USER_PER_DAY = 30       # 单用户每天最多提交条数
MAX_GLOBAL_PER_DAY = 200        # 全站每天最多接收条数（防洪水）
NEW_USER_WINDOW_H = 24          # 新用户（加 bot <24h）配额减半
MIN_TEXT_LEN = 10               # 短于 10 字不处理（无法提取特征）
MAX_TEXT_LEN = 2000             # 长于 2000 字忽略（防大消息卡顿 / DoS）
ABUSE_BAN_THRESHOLD = 10        # 某用户被判 poison/重复 累计超此数 -> 临时封禁 24h

# ---- 分类 / 信誉 ----
AUTO_CANDIDATE_MIN_REPORTERS = 3   # 同一条广告被 >=N 个独立用户转发 -> 直接成候选（仿 MXGA 3 人独立举报）
CATEGORY_PICKER = True             # 转发后是否弹出类别选择按钮（仅校正已有类；新类需 Owner 审批）
