# proactive_strategy_motivation —— 示例策略包（想念驱动主动搭话 / motivation）

X6-c「带独立配额的类别外放」样本：**把「此刻要不要表达渴望、想说什么」搬进插件，
把「给谁发、能不能发、发几次、素材怎么装、怎么生成发送」留在内核**。

想念通道（motivation）与已外放的 `rhythm` / `memory_review` 不同：它有**独立配额**
（近 6h 1 条 + 每日 ≤2 条，不占普通每小时额度）与**独立生成链路**（剧情线 + 想念配额），
所以内核除了「让位 + 去重」还登记了 **内核 prepare**——配额、去重、关系门（渴望度阈值）、
免打扰、素材装配全在内核，策略包只给候选与意图。

## 文件

```
proactive_strategy_motivation/
├── manifest.json   # 类型/权限/context_keys/可改参数
├── main.py         # 策略本体：relationship + user_rhythm + quota + character_state → 想念候选
└── README.md       # 本文件
```

## 边界（写带独立配额的策略包前先看这张表）

| 归内核（**不要写进策略包**） | 归策略包（本目录做的事） |
|---|---|
| 选人（谁开启主动、有没有会话）→ `ctx["roster"]` | 此刻要不要表达渴望（关系 + 作息 + 状态打分） |
| **关系门**：渴望度 < 阈值不发（内核按关系标量算） | 想表达什么（trigger_reason 意图描述） |
| **独立配额**：近 6h 1 条 / 每日 2 条 + 去重 | —（可在包里先自查 `quota` 省一次取数） |
| 免打扰、未回复冷却、每小时限额 | — |
| 素材装配（会话 / 最近消息 / 闲置时长 / 人格现状） | — |
| 剧情线生成、发送、落库 | — |

内核让位后仍会执行 `prepare_strategy_candidate`：本包用的四个 key 只是**判定素材**，
真正的闸门都在内核，插件绕不开。

## 开启步骤

1. 内核 flag `proactive_strategy_plugins` 置 True（默认 False，见 `docs/feature-flags.md`）；
2. 扩展页启用本插件。
关掉任一个 → 内核不再下发 roster，本包返回空，**逐字节回到旧行为**。
flag 开 + 本包启用时，内核 `motivation` 源整体让位，同类只有本包一个生产者（防双发）。

## 只读素材（manifest `context_keys`）

| key | 说明 | 需要 `character_id` |
|---|---|---|
| `quota` | 本类别近 6h / 当日已用数与上限（类别由内核按本包登记推导） | 是 |
| `relationship` | trust / attachment / curiosity（0-100） | 是 |
| `user_rhythm` | 距上次用户消息的小时数 + 已学到的活跃时段与当前权重 | 是 |
| `character_state` | 角色八维（desire / fatigue / mood / …） | 是 |

## 可改参数（manifest `config`，扩展页可直接改）

| 键 | 默认 | 说明 |
|---|---|---|
| `strategy_category` | `"motivation"` | **别改**：内核据此判定「本类别已被接管」并让位 |
| `min_score` | `0.45` | 策略层渴望打分下限（低于则不投；内核阈值另算，两边都要过） |
| `min_idle_hours` | `2.0` | 距上次用户消息不足该小时数 → 不开口（刚聊完就黏人） |
| `tired_fatigue` | `75` | 疲惫抑制基准（越接近该值，打分衰减越强） |

## 候选契约

```python
{
    "character_id": 3, "user_id": 1, "session_id": 42,   # 来自 roster
    "strategy": "motivation",         # 策略类别（内核让位/执行路由/去重依据）
    "message_type": "motivation",     # 落库与执行口径（走内核想念通道，独立配额）
    "behavior": "motivation",
    "trigger_reason": "渴望度0.62/闲置5.5h",  # 只进触发日志，便于观测
}
```

注册口（main.py 加载期）：

```python
sdk.register_proactive_strategy("motivation", message_types=["motivation"])
```

## 常见坑

- **别自己记配额**：想念通道的 6h/当日配额在内核（还会写触发日志），包内节流只会打架；
  包里读 `quota` 只是为了「配额用完就别白取素材」。
- **别自己算渴望度门槛**：内核 prepare 会用关系标量重算渴望度并对阈值，包里打分高也没用。
- **`message_type` 必须在注册白名单内**：否则内核不认，会当普通插件候选（落库 `plugin`）。
- **想念落库是 `storyline`**：按 `proactive_message_logs` 的 message_type 查不到想念消息，
  内核去重因此改用 `proactive_trigger_logs`（`decision=approved`，近 6h），见
  `strategy.CATEGORY_DEDUP`。

参考：`docs/plugin-development.md` §十四「策略包（proactive_strategy）」、
`docs/extension-contract.md` §2 / §4。
