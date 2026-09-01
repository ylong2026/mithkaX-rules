# -*- coding: utf-8 -*-
"""AI 兜底：仅后端 process_pending 在“词典分不出类”的新样本上调用。

公众转发绝不触发本模块。Key 是你的，别人烧不到你的 token。
使用 OpenAI 兼容接口，可指向 DeepSeek / Qwen / 本地模型。
"""
import json
import re
import urllib.request
try:
    from config import AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL
except ImportError:
    from config_defaults import AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL
import common

SYSTEM = (
    "你是 Telegram 垃圾/广告消息的正则规则生成器。"
    "给定一条垃圾消息和候选类别，输出用于匹配同类消息的正则。"
    "规则：只匹配稳定结构，不要绑定具体品牌名/频道名/数字/链接；"
    "使用 (?:a|b) 多选与 .*? 间隙；不要使用 (?i) 内联标志；"
    "正则长度 < 256；返回 JSON：{\"category\":str,\"tokens\":[str],\"regex\":str,\"confidence\":float}。"
)

CATEGORY_LABELS = {
    "airport": "机场/VPN/代理/翻墙",
    "gambling": "赌博/博彩/棋牌",
    "adult": "黄色/卖片/约炮",
    "phishing_scam": "诈骗/杀猪盘/刷单",
    "selling": "卖货/微商/招代理/免费领",
    "finance_illegal": "非法贷款/黑户洗白/套现",
    "proxy_service": "代办/代充/解封/代考",
    "spam_link": "推广链接/拉群/机器人群发",
}


def review(text: str, category_hint):
    """返回 dict 或 None。"""
    if not AI_ENABLED or not AI_API_KEY:
        return None
    labels = ", ".join(f"{k}={v}" for k, v in CATEGORY_LABELS.items())
    user = f"可选类别：{labels}\n候选命中：{category_hint or '未知'}\n消息：{text}"
    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{AI_BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AI_API_KEY}"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        data = json.loads(resp["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        print("AI review err:", e)
        return None
    pat = data.get("regex")
    if not pat or not common.safe_compile(pat):
        return None
    return data
