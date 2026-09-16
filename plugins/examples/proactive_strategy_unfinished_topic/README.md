# proactive_strategy_unfinished_topic —— 示例策略包（对话未收尾跟进）

X6-c「带独立配额的类别外放」样本：**把「哪个话头值得追问」搬进插件，
把「给谁发、能不能发、一天几条、话头正文是什么、怎么生成发送」留在内核**。

内核原 `unfinished_topic` 是关键词扫用户最后一句话（下次/改天/有空）；外放后改为从
`open_topics`（话题追踪里进行中且时效内的话头）里挑，覆盖面更广。**两个口径必须先钉死**：
落库 `message_type=unfinished_topic`、当日去重 = 该 `message_type` 的北京当日已发送行
（本类不是剧情线，这条闸会真的命中，不像 rhythm 那样空转）。

## 文件

```
proactive_strategy_unfinished_topic/
├── manifest.json   # 类型/权限/context_keys/可改参数
├── main.py         # 策略本体：open_topics（+ recent_intents 防撞车）→ 话头候选
└── README.md       # 本文件
```

## 边界（写带独立配额的策略包前先看这张表）

| 归内核（**不要写进策略包**） | 归策略包（本目录做的事） |
|---|---|
| 选人（谁开启主动、有没有会话）→ `ctx["roster"]` | 哪个话头值得追问（重要度 + 冷却时长） |
| **每日 1 条配额** + 当日去重 | —（只投 `topic_id`，不投正文） |
| **最小间隔**（距会话最后一条消息 2 小时） | — |
| 免打扰 / 未回复冷却 | — |
| **话头正文**：按 `topic_id` 复核归属与状态后装配 | — |
| 生成（`run_unfinished_topic`）、发送、落库 | — |

## 开启步骤

1. 内核 flag `proactive_strategy_plugins` 置 True（默认 False，见 `docs/feature-flags.md`）；
2. 扩展页启用本插件。
关掉任一个 → 内核不再下发 roster，本包返回空，**逐字节回到旧行为**。
flag 开 + 本包启用时，内核 `unfinished_topic` 源整体让位，同类只有本包一个生产者（防双发）。

## 只读素材（manifest `context_keys`）

| key | 说明 | 需要 `character_id` |
|---|---|---|
| `open_topics` | 进行中且时效内的话题（id / 文本 / 重要度 / 是否目标 / 距上次提及小时数） | 是 |
| `recent_intents` | 未完成的前瞻意图（只用于避开已被追的话题） | 是 |

## 可改参数（manifest `config`，扩展页可直接改）

| 键 | 默认 | 说明 |
|---|---|---|
| `strategy_category` | `"unfinished_topic"` | **别改**：内核据此判定「本类别已被接管」并让位 |
| `min_importance` | `0.6` | 话题重要度下限（与内核建档阈值同量级） |
| `min_hours_since` | `2.0` | 话头至少冷了多久才追（内核另有 2h 最小间隔兜底） |
| `max_per_char` | `1` | 每角色每 tick 最多投几条（内核每日只放行 1 条） |

## 候选契约

```python
{
    "character_id": 3, "user_id": 1, "session_id": 42,   # 来自 roster
    "strategy": "unfinished_topic",   # 策略类别（内核让位/执行路由/去重依据）
    "message_type": "unfinished_topic",  # 落库与去重用口径，别改
    "topic_id": 88,                   # 只给 id：正文由内核复核后装配
    "trigger_reason": "未收尾话题: …",  # 只进触发日志
}
```

注册口（main.py 加载期）：

```python
sdk.register_proactive_strategy("unfinished_topic", message_types=["unfinished_topic"])
```

## 常见坑

- **别自己拼话头正文**：正文由内核按 `topic_id` 复核（归属/状态/时效）后截断，
  包里直接塞文本既可能过期也可能串角色。
- **别自己记「今天问过没」**：内核按 (角色, `unfinished_topic`, 北京日界) 去重，
  加上每日 1 条配额与 2 小时最小间隔。
- **避开前瞻意图**：同一件事若已有 pending 意图在追（`recent_intents`），就别再追问，
  否则会和 `prospective_intent` 通道双线追同一件事。

参考：`docs/plugin-development.md` §十四「策略包（proactive_strategy）」、
`docs/extension-contract.md` §2 / §4。
