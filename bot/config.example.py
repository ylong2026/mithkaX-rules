# -*- coding: utf-8 -*-
# 复制本文件为 config.py 并填入你的值（config.py 已被 .gitignore 忽略，不会上传）。
# 也可通过环境变量注入，避免把密钥写进文件。

import os

# ---- Telegram 机器人 ----
# BotFather 给你的 token。环境变量：ADF_BOT_TOKEN
BOT_TOKEN = os.environ.get("ADF_BOT_TOKEN", "在此填 BotFather 给你的 token")

# 你的 Telegram 数字 ID（给 @userinfobot 发 /start 可查）。环境变量：ADF_OWNER_ID
OWNER_ID = int(os.environ.get("ADF_OWNER_ID", "0"))

# 可信任用户（可直连命令 / 免限流）。填数字 ID 列表。
ALLOWED_USERS = [OWNER_ID]

# ---- GitHub（仅 Owner 后端写规则用；仓库已用 token 克隆，git push 即用本地凭证）----
GITHUB_REPO = "ylong2026/mithkaX-rules"
GITHUB_BRANCH = "main"

# ---- AI（仅后端 process_pending 用，公众转发绝不触发）----
# 设 True 才在“词典分不出类”的新样本上调 AI；False 则新样本只进 needs_review 等你人工看。
AI_ENABLED = False
# OpenAI 兼容端点，可换 DeepSeek / Qwen / 你本地模型。环境变量：ADF_AI_BASE_URL
AI_BASE_URL = os.environ.get("ADF_AI_BASE_URL", "https://api.deepseek.com/v1")
# 你的 Key。环境变量：ADF_AI_KEY
AI_API_KEY = os.environ.get("ADF_AI_KEY", "")
# 模型名（轻量分类模型即可）
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
AUTO_CANDIDATE_MIN_REPORTERS = 3   # 同一条广告被 >=N 个独立用户转发 -> 直接成候选（高置信，仿 MXGA 3 人独立举报）
CATEGORY_PICKER = True             # 转发后是否弹出类别选择按钮（仅校正已有类；新类需 Owner 审批）
