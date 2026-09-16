# proactive_strategy_special —— 示例策略包（节日 / 生日 / 认识纪念日祝福）

X6「主动内容策略外放」的第一个样本：**把「发什么、什么时候发」搬进插件，「给谁发、
能不能发、发几次」留在内核**。可直接复制本目录改名后改写。

## 文件

```
proactive_strategy_special/
├── manifest.json   # 类型/权限/可改参数（策略参数全在 config 里）
├── main.py         # 策略本体：roster → 候选（纯判定 + 模板）
└── README.md       # 本文件
```

## 边界（写策略包前先看这张表）

| 归内核（**不要写进策略包**） | 归策略包（本目录做的事） |
|---|---|
| 选人（谁开启主动、有没有会话）→ `ctx["roster"]` | 今天该不该发（日期判定） |
| 频控、去重、免打扰、睡眠、关系门 | 发哪一类（生日 / 节日 / 纪念日） |
| 生成、发送、落库、限额 | 文案怎么说（模板 → `hint`） |

## 开启步骤

1. 内核 flag `proactive_strategy_plugins` 置 True（默认 False，见 `docs/feature-flags.md`）；
2. 扩展页启用本插件。
关掉任一个 → 内核不再下发 roster，本包返回空，**逐字节回到旧行为**。
flag 开 + 本包启用时，内核 `special` 源整体让位，同类祝福只有本包一个生产者（防双发）。

## 可改参数（manifest `config`，扩展页可直接改）

| 键 | 默认 | 说明 |
|---|---|---|
| `strategy_category` | `"special"` | **别改**：内核据此判定「本类别已被接管」并让位 |
| `kinds` | `["birthday","holiday","anniversary"]` | 允许发哪几类 |
| `lead_days` | `0` | 提前几天发（只对生日/节日生效；纪念日是已发生的第 N 天，不支持提前） |
| `festivals` | 7 个常见节日 | `MM-DD` → 节日名（策略包自带，不依赖内核日历） |
| `templates` | 三类中英模板 | 占位符 `{nickname}` `{character_name}` `{holiday_name}` `{days}` |

## 候选契约（返回 list[dict]，每项必填）

```python
{
    "character_id": 3, "user_id": 1, "session_id": 42,   # 会话定位（来自 roster）
    "strategy": "special",        # 策略类别（内核让位/去重的依据）
    "message_type": "holiday",    # 落库口径（special 只认 birthday/holiday/anniversary）
    "hint": "今天是 国庆节，……",   # 文案意图，内核生成并发送
    "holiday_name": "国庆节",      # 可选，写进主动消息日志
}
```

## 最小示例（30 行以内，抄这段就能跑）

```python
import datetime
from app.plugins import sdk

@sdk.hook("proactive_candidate")
async def strategy(ctx):
    if "special" not in (ctx or {}).get("strategy_categories", []):
        return None                       # flag 关 → 零输出
    cfg = sdk.get_config() or {}
    today = datetime.date.today()
    out = []
    for e in (ctx or {}).get("roster") or []:
        name = (cfg.get("festivals") or {}).get(today.strftime("%m-%d"))
        if not name or not e.get("holiday_enabled"):
            continue
        out.append({
            "character_id": e["character_id"], "user_id": e["user_id"],
            "session_id": e["session_id"], "strategy": "special",
            "message_type": "holiday", "holiday_name": name,
            "hint": f"今天是{name}，给{e.get('nickname')}一句节日祝福",
        })
    return out or None
```

## 常见坑

- **双发**：类别被接管后内核同名源会整体让位，**不要**再在插件里自己调 `sdk.send_message`
  （那会绕过内核频控，且和内核那条并存）。
- **越权频控**：别在插件里记「今天发过了」——内核已按 `(角色, message_type, 北京日界)` 去重，
  插件侧的额外节流只会让两边的口径打架。
- **缺 `strategy` / `message_type`**：内核会把它当普通插件候选（落库 `message_type=plugin`），
  不再享受让位与去重，容易重复发送。
- **无 `permissions`**：本包不自己发消息，不需要 `send_message`；需要写记忆才加 `write_memory`。

参考：`docs/plugin-development.md` §十四「策略包（proactive_strategy）」、
`docs/extension-contract.md` §2（proactive_candidate 契约）。
