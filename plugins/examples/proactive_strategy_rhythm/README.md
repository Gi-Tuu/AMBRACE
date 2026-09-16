# proactive_strategy_rhythm —— 示例策略包（随机想起 / 节律）

X6-b「状态型策略外放」的样本：**把「今天该不该发起、发起哪一类行为」搬进插件，
「给谁发、能不能发、发几次、素材怎么装、怎么生成发送」留在内核**。

与 `proactive_strategy_special`（日期型策略）的区别：本包吃的是**状态型素材**
（`time_ctx` 时段 / `character_state` 角色状态），判定结果是一个**行为类别**（behavior），
由内核按该行为的既定执行链走（剧情线 / 朋友圈等）。

## 文件

```
proactive_strategy_rhythm/
├── manifest.json   # 类型/权限/context_keys/可改参数
├── main.py         # 策略本体：time_ctx + character_state → 行为候选
└── README.md       # 本文件
```

## 边界（写状态型策略包前先看这张表）

| 归内核（**不要写进策略包**） | 归策略包（本目录做的事） |
|---|---|
| 选人（谁开启主动、有没有会话）→ `ctx["roster"]` | 今天该不该发起（时段 + 概率 + 状态） |
| 每日上限、pending 计时器 / 未发完剧情线互斥 | 发起哪一类行为（behavior） |
| 素材装配（会话 / 最近消息 / 闲置时长 / 人格现状） | 只给 `behavior` + `hint` 意图 |
| 免打扰、睡眠、最小间隔、每小时限额、未回复冷却 | — |
| 剧情线生成、发送、落库 | — |

内核让位后仍会执行 `prepare_strategy_candidate`：`character_state` 只是**判定素材**，
真正的闸门（每日上限等）与素材装配都在内核，插件绕不开。

## 开启步骤

1. 内核 flag `proactive_strategy_plugins` 置 True（默认 False，见 `docs/feature-flags.md`）；
2. 扩展页启用本插件。
关掉任一个 → 内核不再下发 roster，本包返回空，**逐字节回到旧行为**。
flag 开 + 本包启用时，内核 `rhythm` 源整体让位，同类只有本包一个生产者（防双发）。

## 只读素材（manifest `context_keys`）

| key | 说明 | 需要 `character_id` |
|---|---|---|
| `time_ctx` | 北京日期 / 小时 / 时段名 / 时段倾向 / 是否周末 | 否 |
| `character_state` | 角色八维状态（mood/fatigue/comfort/…） | 是 |

未声明的 key 一律取不到（白名单在内核侧过滤）；单 key 取数失败只丢该 key，不阻塞主链路。

## 可改参数（manifest `config`，扩展页可直接改）

| 键 | 默认 | 说明 |
|---|---|---|
| `strategy_category` | `"rhythm"` | **别改**：内核据此判定「本类别已被接管」并让位 |
| `probability` | `0.4` | 全局发起概率（每 tick 每角色；内核还有每日上限/最小间隔兜底） |
| `windows` | 七档时段概率 | 时段名（清晨/上午/午间/下午/傍晚/晚间/深夜）→ 概率，覆盖全局值 |
| `behaviors` | 七档行为表 | 时段名 → 候选行为（首选 70% / 次选 30%） |
| `tired_fatigue` | `75` | 疲惫 ≥ 该值且候选含 `goodnight` → 优先晚安 |
| `happy_mood` | `75` | 心情 ≥ 该值且候选含 `status_update` → 优先状态分享 |

## 候选契约

```python
{
    "character_id": 3, "user_id": 1, "session_id": 42,   # 来自 roster
    "strategy": "rhythm",             # 策略类别（内核让位/执行路由/去重依据）
    "message_type": "proactive_chat", # 行为（同时是内核执行路由的事件类型）
    "behavior": "proactive_chat",     # 便于观测；与 message_type 一致
    "hint": "现在是晚间，……",          # 意图（剧情线路径由内核生成，hint 供观测/兜底）
}
```

注册口（main.py 加载期）：

```python
sdk.register_proactive_strategy(
    "rhythm",
    message_types=["greeting", "status_update", "proactive_chat", "goodnight",
                   "moment_publish", "moment_comment"],
)
```

## 常见坑

- **别自己装配素材**：人格/现状/最近消息由内核补齐，插件只给 `character_id` + `behavior`；
  自己在插件里查会话/消息既重复又容易与内核口径打架。
- **别自己记「今天发过没」**：内核有每日上限 + pending 剧情线互斥 + 最小间隔，插件侧节流只会打架。
- **`message_type` 必须在注册白名单内**：否则内核不认，会当普通插件候选（落库 `plugin`）。
- **本类别的同日去重**：节律落库口径是 `storyline`，按 `message_type` 的去重闸对它无效，
  同日防轰炸靠内核每日上限 + 最小间隔（与迁移前一致）。

参考：`docs/plugin-development.md` §十四「策略包（proactive_strategy）」、
`docs/extension-contract.md` §2 / §4。
