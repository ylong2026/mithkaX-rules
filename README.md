# mithkaX-rules

Community ad / spam regex rules for **MithkaX** (a Telegram client). The bot
turns spam you forward into regex rules; this repo is the **storage + source
of truth**. The App subscribes to the raw URL below and pulls on a timer.

## 订阅地址（App 里填这个）

```
https://raw.githubusercontent.com/ylong2026/mithkaX-rules/main/rules.json
```

App 侧：设置 → 拦截 → 广告过滤规则库 URL，填上面这行；开启自动同步（默认 30 分钟），或点「立即拉取」。

## 文件结构

| 文件 | 作用 |
|---|---|
| `categories.json` | **人类可编辑的真相源**。按大类组织规则 + `allow` 白名单 + `external` 外部源声明 |
| `rules.json` | **App 实际订阅的扁平文件**，由 `build.py` 生成，不要手改 |
| `build.py` | 合并 `categories.json` + `external/*.json` → `rules.json`（带 version/updatedAt/ruleCount） |
| `external/nagram.json` | 从 Nagram 社区规则导入的转换结果（见下） |
| `nagram_sync.py` | 把 Nagram 的 PCRE 规则转成我们的格式 |
| `schemas/rules.schema.json` | `rules.json` 的 JSON Schema |
| `bot/DEFENSE.md` | 机器人端防滥用 / AI 限流 / 贡献审核设计 |

## 闭环（你在 Telegram 里转发广告 → 自动屏蔽）

```
你转发疑似广告 ──▶ 机器人(鉴权→分类→生成安全正则→去重→批量commit)
                        │
                        ▼
              mithkaX-rules/rules.json  (本仓库, 公开 raw URL)
                        │
                        ▼  (30min 定时 / 手动拉取)
              App 本地缓存 ──▶ 聊天 / 通知 / 会话列表 三处过滤
```

## 规则大类（category）

规则按类组织，便于**整类开关**与**增量加类**。当前 8 类（对齐 Nagram 的思路）：

`airport`(机场/VPN) · `gambling`(赌博) · `adult`(黄色) · `phishing_scam`(诈骗)
`selling`(卖货/招代理) · `finance_illegal`(非法贷款) · `proxy_service`(代办/解封) · `spam_link`(推广链接)

**加新类**：在 `categories.json` 的 `categories` 数组里加一个对象即可，`build.py`
会自动带进 `rules.json`。Nagram 以后扩类，同理。

## 白名单（allow-list，防误封）

`categories.json` 顶层 `allow` 数组里的规则是**例外**：命中它的消息**永不屏蔽**，
即使某条 block 规则也命中（例外优先于屏蔽）。这是让激进的社区列表保持精准、
不误伤正常内容的关键。语法与 block 一样：`domain:`、`re:`、`sender:`、`keyword:`。

App 侧已支持：白名单规则在引擎里独立编译，`shouldBlock` 先判白名单再判屏蔽。

## 规则语法（一条一行，兼容 Nagram）

```
re:机场.*?(?:节点|VPN)        # 正则（默认大小写不敏感）
(?i)机场.*?VPN                # Nagram 风格 (?i) —— App 会自动剥掉 (?i) 并转小写
domain:example.com            # 命中消息里出现的该域名（含 https:// / t.me/）
sender:123456789              # 屏蔽某发送者（机器人可由转发来源频道自动提取）
keyword:免费领                 # 纯关键词
allow:domain:github.com       # 白名单例外
# 这是注释
```

## 别人怎么贡献？（贡献 / 防滥用模型）

机器人**免费开放**给大家用，但写入规则仓库是分层的，防止投毒与滥用：

| 角色 | 能做什么 | 写入方式 |
|---|---|---|
| **Owner（你）** | 转发即自动生成 + 直接 commit | 直写 `rules.json` |
| **公众用户（可信）** | 贡献规则 | 进 `allow`/审核队列或提 PR，Owner 合并 |
| **公众用户（默认）** | 仅用机器人**检测/查询**（这条消息会不会被屏蔽、属于哪类） | 只读，不能直接写仓库 |

推荐落地：**公众转发先落 `pending/`（PR 或隔离文件），Owner 审阅后合并**，
绝不让匿名转发直接 commit。详细见 `bot/DEFENSE.md`。

## AI 怎么用、怎么防刷？

- **AI 是兜底不是核心**：分类优先用词典（机场/VPN/赌博…），命中即用该类**预置安全模板正则**；只有词典判不出的模糊样本才调 AI 提取稳定特征。
- **谁的 AI / 免费**：Owner 用自己的 Key（预算内）；公众用户要么自带 Key，要么走**零成本的词典路径**（不让别人烧你的 token）。
- **防 token 轰炸**：① 按 `from.id` 限流（每分钟/每天上限）；② 重复消息哈希直接复用、跳过 AI；③ 短文本/已命中现有规则的跳过 AI；④ 每日 AI 调用预算封顶，超了降级为词典-only 或拒绝；⑤ 缓存 AI 结果。
- 免费替代：纯词典分类 + 轻量本地模型（如小参数中文分类器）可完全不调付费 API。

## 与 Nagram 兼容 / 一键同步

Nagram 用的是 PCRE（`(?i)` 内联 flag），Dart 不认 → 我们的 `regex_engine`
会在加载时**自动剥掉 `(?i)`** 并转小写，所以 Nagram 规则能直接吃。

同步步骤：
1. 把 Nagram 的规则（一类一条）粘到 `nagram_raw.txt`；
2. `python3 nagram_sync.py`（或 `--fetch` 尝试在线抓）；
3. `python3 build.py` 重新生成 `rules.json`；
4. commit & push。

Nagram 扩类时重复 1–4 即可，增量合并、不去重冲突。

## 规则智能增删（防止膨胀到上万条）

核心是**按「稳定特征」生成规则，不按字面**：
- 归一化消息（小写、全角→半角、去 URL/数字/@/零宽、压扁空白）；
- 提取**类别词 + 动作词**组合作为「签名」，生成**通用正则**（如 `(?:棋牌|博彩).*?(?:首充|充值)`）；
- **去重键 = (类别, 排序后的签名 token)**：同签名的不同变体（改几个字、重排列）落到**同一条规则**，不新增；
- 仅当签名全新（新类或新稳定词组合）才加规则；
- 规则带 `addedAt` 与命中计数，长期 0 命中的候选清理，保持精简。

所以「同一广告改字重排」→ 命中已有通用规则，**不重复存储**；与你本地已屏蔽的
并不冲突——你本地 `KeywordBlocker` 是私人规则，本仓库是社区自动规则，两层互补。

## 本地校验

```bash
python3 build.py                       # 重新生成 rules.json
python3 -c "import json,sys; json.load(open('rules.json')); print('rules.json OK')"
```
