# MithkaX 广告过滤机器人

把电报里发现的广告/垃圾消息**转发**给机器人，机器人只把它存进待审核队列；
**你（Owner）的后端定时脚本**用你自己的 AI Key 做分类+去重+生成候选，你复核后入库。
公众转发**绝不触发 AI、绝不直接写规则**，从源头杜绝投毒与 token 浪费。

## 数据流

```
任何人转发广告 → 机器人(限流+去毒+去重) → pending/ 队列（主通道，自带来源证明）
任何人粘贴广告 → 机器人 → unverified/ 队列（低信任，需 Owner 复核才计入）
        ↓ (Owner 后端每 4-6h 定时跑)
   process_pending.py
     · 分类+去重+沙箱自测，AI 仅在分不出类时兜底
     · >=N 独立用户转发同一条 → 直接成候选（高置信）
     · 类无法归入现有 8 类 → 写 category_proposals/ 提案（需 Owner 审批）
        ↓
   candidates/candidate_rules.json + review_<日期>.md
        ↓ (Owner 复核：人审 / 自己看 AI 结论 / 审批新类别 /resolve 申诉)
   apply_candidates.py --apply  →  inbox_rules.json(带 provenance)  →  build.py  →  rules.json  →  push
        ↓
   App 定时拉取 rules.json 屏蔽（规则含 category，未来可分类开关）
```

### 分类策略（借鉴 MXGA 治理铁律）

- **不强制用户选类**：转发即进队列，后台自动分类（词典+AI）。
- **可选校正**：转发后弹出类别按钮，点了 = 高信任权重（仅校正“已有类”）。
- **已有类**：用户可自由打标；**新类**：AI/后台检测到不属于现有类的簇时进提案队列，**只有 Owner 审批**（`/addcat`）才进 categories.json，防范围 creep / 投毒。
- **公开规则带证据**：每条 rules.json 规则附 `category` / `evidence`（脱敏样本）/ `reporters`（独立举报人数，不泄露用户 ID）/ `firstSeen`，git 提交即审计链。

## 部署

```bash
cd mithkaX-rules
cp bot/config.example.py bot/config.py      # 填 BOT_TOKEN / OWNER_ID / (可选)AI_*
python3 bot/bot.py                           # 一直跑；建议用 nohup / systemd / tmux
```

定时处理（在你自己机器上，用你的 AI Key）：

```bash
# 每 4 小时跑一次（注意时区）
0 */4 * * * cd /绝对路径/mithkaX-rules && python3 bot/process_pending.py >> bot/cron.log 2>&1
```

Owner 复核入库：

```bash
python3 bot/apply_candidates.py              # 先预览
python3 bot/apply_candidates.py --apply      # 确认后入库+提交推送
```

## Owner 命令（私聊机器人，需信任身份）

- `/stats` 队列条数（待审核 / 低信任 / 申诉）
- `/review` 提示怎么处理
- `/process` 立即跑一次后端
- `/allow <数字ID>` 信任某用户（免限流、可命令）
- `/proposals` 列出待审批的新类别提案
- `/addcat <id> <label>` 审批通过某个新类别，加入 categories.json 并重建
- `/appeals` 列出用户申诉
- `/resolve <hash>` 处理申诉：移除匹配该消息的入库规则并重建

## 申诉（误拦闭环）

用户认为某条正常消息被误拦：先发 `/appeal`，再把那条消息**转发**给机器人，进入 `appeals/`。
你复核后：若属实，`/resolve <hash>` 会移除 inbox_rules.json 中匹配该消息的规则并重建推送。

## 防滥用设计（为什么安全）

| 措施 | 说明 |
|---|---|
| 转发=数据，非指令 | 机器人**绝不**把转发文本当命令/规则解析；有人转发 `allow:xxx` 只会当作一条垃圾样本分析 |
| 来源证明 | 转发自带 `forward_origin`（来自哪个频道/用户），几乎不可伪造——比 MXGA 的 GitHub token 身份更轻量 |
| 主通道=转发，粘贴=低信任 | 粘贴进 unverified/，不计入公开规则，需 Owner 复核 |
| 公众只写 pending | 队列不进 rules.json，不直接 commit，无法投毒线上规则 |
| 限流 | 单用户 每分钟/每天 上限；新用户减半；全站每日上限防洪水 |
| 去重 + 多举报置信 | 同消息多人转发只计一次；`>=N` 独立转发同签名直接成候选（仿 MXGA 3 人独立举报） |
| 去毒 | 含“不要屏蔽/加白名单”+联系方式的操纵性提交进 poison/，不入库 |
| AI 仅后端 | 公众转发不调 AI；AI 只在“词典分不出类”的新样本兜底，用 Owner 自己的 Key |
| 沙箱自测 | 生成的每条规则必须命中样本且不误伤正常样本，否则标 flagged 等你审 |
| 新类别闸门 | 用户不能随便建类；新类提案需 Owner 审批，避免范围 creep / 投毒 |
| 人工兜底 | `needs_review` 永远需你决定，不自动上线 |

## 与主流对比

- **uBlock / EasyList**：社区提 Issue/PR → 维护者人审 → 无自动提交。我们更严：公众提交只进队列、仅 Owner 入库。
- **Spamhaus / SpamCop**：用户举报 → 评分引擎 → 高置信自动入、低置信人审。我们同理，但用“Owner 复核”替代多人众包。
- **Telegram @shieldy / @combot**：新成员验证码 + 用户 /report + ML + 信誉自动封。我们复用“转发=举报”+ 限流 + 信誉封禁。
- **Cloudflare**：限流 + 信誉 + 挑战。我们用限流 + 操纵性短语检测 + 临时封禁。

共性：**不可信输入 → 队列 → 评分 → 高置信自动 / 低置信人审 → 限流举报者 → 信誉**。
你的设计完全对齐主流，只是把“众包维护”收敛成“个人 Owner 维护”，更适合私人规则库。
