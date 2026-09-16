# 游戏扩展包模板（game_template）

一个**可以直接跑、也可以照抄改成你自己的游戏**的最小插件。当前实现的是「猜数字」，
用来演示一个合规游戏扩展包需要写什么。

> 内核与扩展包的边界：房间、回合、主持、游戏记忆隔离由 `app/games/` 基座提供；
> **扩展包只负责写规则引擎**（一个 `GameEngine` 子类 + 一次 `sdk.register_game` 注册）。
> 你**不需要**、也**不应该**碰内核代码。

## 文件结构

```
game_template/
├── manifest.json   # 插件元信息（type=http 声明型；游戏包无需 hook/permission）
├── main.py         # 引擎类（实现 GameEngine 全接口）+ 末尾 sdk.register_game(...) 注册
└── README.md       # 本文件
```

## 改造四步

1. **改 `manifest.json`**：把 `name` 改成一个全局唯一、`A-Za-z0-9_-` 组成的名字（≤64 字符），
   并改 `description` / `author`。
2. **改引擎类**：重命名 `NumberGuessEngine`，并修改类属性
   `game_type`（2-24 位小写字母/数字/下划线）、`player_mode`（single/dual/multi）、
   `min_players` / `max_players`（0 ≤ min ≤ max ≤ 16）、`needs_gm`。
3. **改写九个方法**（下面「必需接口」逐条说明）。
4. **改注册调用**：文件末尾 `sdk.register_game("number_guess", NumberGuessEngine, {...})` 的
   第一个参数 `game_type` 必须与类属性一致，meta 字典的必填字段与类属性对齐。

## 必需接口（必须写）

`GameEngine` 是抽象基类（`backend/app/games/base.py`），以下方法**全部 abstractmethod，缺一不可**：

| 方法 | 作用 | 本模板做法 |
|---|---|---|
| `setup(player_seats)` | 发牌/分配角色/初始化状态，返回初始公开事件 | 设定出题人/猜题人、秘密、`stage` |
| `apply_action(seat, action, payload)` | 校验并执行一个玩家动作，返回 `ActionResult` | 出题 / 猜数，非法动作 `ok=False` |
| `advance()` | 阶段推进，返回新事件列表 | 本游戏逐步裁决，`advance` 返回 `[]` |
| `check_winner()` | 返回胜利方 `"seat_X"` 或 `None` | `stage=="done"` 时返回胜者 |
| `timeout()` | 超时自动推进（调兜底动作） | 调 `fallback_action` 后 `apply_action` |
| `view_for(seat)` | 构造某玩家的 `PlayerView`（信息隔离核心） | 秘密只给出题人，猜题人 `private={}` |
| `build_ai_prompt(seat)` | 组装 AI 玩家的 LLM 上下文（只含可见信息） | 规则摘要 + 该玩家可见事件/视图 |
| `expected_action(seat)` | 当前该玩家该做什么（驱动 AI） | `"set_secret"` / `"guess"` / `"skip"` |
| `fallback_action(seat)` | LLM 失败时的兜底合法动作（不阻塞） | 随机选秘密 / 随机猜 |

另外 `current_turn_seat()` 基类默认抛 `NotImplementedError`，**你也必须覆盖**它
（返回当前应行动的座次，`None` = 等阶段流转/已结束）。`coin_flip` 模板里同样覆盖了它。

## 可选 / 通用（基类已给默认实现，按需覆盖）

- `load` / `persist_event` / `persist_state` / `finish`：从 DB 恢复与落库，默认实现已够用。
- `public_events` / `public_events_for` / `my_events`：可见事件过滤，默认按 `visibility` 工作。
- `apply_surrender(seat)`：通用投降（确定性、零 LLM），默认实现已够用，一般不用改。
- `has_draw_semantics`（类属性，默认 `True`）：无平局语义的引擎可置 `False`。

## 私有信息怎么隔离

- 引擎运行时状态放 `self.state`（dict，会随 `state_json` 持久化）。
- 某玩家**独享**的信息放该玩家的 `private_json`（如本模板出题人的秘密数字）。
- `view_for(seat)` 构造 `PlayerView` 时，**只把该玩家该看到的内容放进 `private`**；
  其他人的私有信息绝对不能出现（本模板：猜题人的 `private` 永远是 `{}`）。
- AI 上下文 `build_ai_prompt` 同样只能引用 `view_for` / `public_events_for` 给到的可见信息，
  不能偷看别人 `private_json`——否则就破坏了信息隔离。

## 注册时机（重要）

`sdk.register_game(...)` **只能在插件 `main.py` 被加载时调用**（基座会校验当前插件上下文）。
放文件顶层即可，不要延迟到函数里调用——否则会报
`RuntimeError: sdk.register_game 只能在插件 main.py 加载时调用`。

## 安装 / 分发

- 随仓库分发（进开源包）：放在 `plugins/examples/<name>/`，服务器启动扫描即自动加载。
- 用户自己装：打包成 zip（`manifest.json` 在根目录），扩展页上传或
  `POST /api/v1/plugins/install`；或解压到 `backend/data/plugins/<name>/` 后重新扫描。
- 发布到远程市场：见 `docs/plugin-development.md` 附录与市场 `index.json` 规范。
- 插件**停用**后其游戏自动从游戏列表隐藏；插件目录**移除**后残留注册由
  `sync_plugins_db` 清理——你无需手动注销。
