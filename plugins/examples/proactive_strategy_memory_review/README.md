# proactive_strategy_memory_review —— 示例策略包（主动到期复习）

X6-b「状态型策略外放」的第二个样本：**只把「复习哪条记忆」搬进插件**，
其余（日上限 / 抽检间隔 / 时态闸门 / 生成 / 发送 / 复习成功判定）**全部仍在内核**
`run_memory_review` 里——策略包绕不开，也不该绕。

## 文件

```
proactive_strategy_memory_review/
├── manifest.json   # 类型/权限/context_keys/可改参数
├── main.py         # 策略本体：due_reviews → 复习候选
└── README.md       # 本文件
```

## 边界

| 归内核（**不要写进策略包**） | 归策略包（本目录做的事） |
|---|---|
| 选人（谁开启主动、有没有会话）→ `ctx["roster"]` | 复习哪条到期记忆 |
| 到期口径（importance / 时态 / 活跃会话过滤）→ `due_reviews` | 重要度下限（`min_importance`） |
| 日上限、抽检间隔、时态闸门、复习成功判定 | — |
| 去重 / 免打扰 / 生成 / 发送 / 落库 | — |

## 开启步骤

1. 内核 flag `proactive_strategy_plugins` 置 True（默认 False，见 `docs/feature-flags.md`）；
2. 扩展页启用本插件。
关掉任一个 → 内核不再下发 roster，本包返回空，**逐字节回到旧行为**。
flag 开 + 本包启用时，内核 `memory_review` 源整体让位，同类只有本包一个生产者（防双发）。

## 只读素材（manifest `context_keys`）

| key | 说明 | 需要 `character_id` |
|---|---|---|
| `due_reviews` | 该角色当前到期/待复习的记忆（id + 摘要 + 重要度 + 到期时间，≤3 条） | 是 |

`due_reviews` 复用内核 `collect_review_events` 的到期与时态口径，且**只返回该角色自己的条目**
（不传 `character_id` 则不下发，防跨角色取数）。

## 可改参数（manifest `config`，扩展页可直接改）

| 键 | 默认 | 说明 |
|---|---|---|
| `strategy_category` | `"memory_review"` | **别改**：内核据此判定「本类别已被接管」并让位 |
| `max_per_char` | `1` | 每角色每 tick 最多投几条（默认与内核一致：1 条） |
| `min_importance` | `40` | 低于该重要度的到期条目不投（默认与内核 `REVIEW_MIN_IMPORTANCE` 一致） |

## 候选契约

```python
{
    "character_id": 3, "user_id": 1, "session_id": 42,   # 来自 roster
    "strategy": "memory_review",      # 策略类别（内核让位/执行路由/去重依据）
    "message_type": "memory_review",  # **沿用内核既有口径，不要新建一套**
    "memory_id": 123,                 # 执行归内核：run_memory_review(char, user, memory_id)
}
```

注册口（main.py 加载期）：

```python
sdk.register_proactive_strategy("memory_review", message_types=["memory_review"])
```

## 常见坑

- **别新建 message_type**：复习落库口径就是 `memory_review`，换名会让内核去重与统计失效。
- **别自己生成复习文案**：`run_memory_review` 里的回忆化提示词 / 时态闸门 / 成功判定必须走内核。
- **别在插件里做日上限**：限额与抽检间隔在 `run_memory_review` 内，插件侧节流只会让两边打架。

参考：`docs/plugin-development.md` §十四「策略包（proactive_strategy）」、
`docs/extension-contract.md` §2 / §4。
