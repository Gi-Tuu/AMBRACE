// 服务器「高级开关」目录：模块分组 + 中文说明（2026-08-30，v3.4.0）
//
// 说明：
// - 这里只做「展示文案与分组」，开关真源仍是后端 AGENT_FLAGS / FeatureFlagService；
// - 顶部 4 个常用开关不在本目录，仍由 FeatureFlagsScreen._visibleKeys 管理；
// - 后端新增、这里未登记的键会自动进入「其他高级开关」兜底组，不会丢失。
// 纯展示文案/分组数据，无需依赖 Flutter UI 库。

class FlagMeta {
  /// 中文名称（一行）
  final String title;

  /// 简短说明（界面默认最多显示两行）
  final String short_;

  /// 完整说明（点「详情」展开）；为空则不显示详情按钮
  final String detail;

  const FlagMeta({required this.title, required this.short_, this.detail = ''});
}

class FlagGroup {
  final String title;
  final List<String> keys;
  const FlagGroup(this.title, this.keys);
}

class FeatureFlagCatalog {
  FeatureFlagCatalog._();

  /// 单个 flag 的中文元数据。未登记键给通用兜底文案。
  static FlagMeta metaOf(String key) {
    return _metas[key] ??
        FlagMeta(
          title: key,
          short_: '高级运行时开关，普通使用保持默认即可，无需调整。',
          detail: '原始标识：$key\n\n这是一个未内置中文说明的开发/调试开关。'
              '若不确定它的作用，请保持默认，或查阅后端 AGENT_FLAGS 中的注释。',
        );
  }

  /// 依据「后端实际存在的高级键集合」产出有序分组（空组自动隐藏，未知键进兜底组）。
  static List<FlagGroup> groupEntries(Set<String> presentAdvancedKeys) {
    final used = <String>{};
    final out = <FlagGroup>[];
    for (final g in _groupOrder) {
      final ks = g.keys.where(presentAdvancedKeys.contains).toList();
      if (ks.isNotEmpty) {
        used.addAll(ks);
        out.add(FlagGroup(g.title, ks));
      }
    }
    final rest = presentAdvancedKeys.where((k) => !used.contains(k)).toList()..sort();
    if (rest.isNotEmpty) out.add(FlagGroup('其他高级开关', rest));
    return out;
  }

  // ── 分组顺序（即界面从上到下顺序）──────────────────────────────
  static const List<FlagGroup> _groupOrder = [
    FlagGroup('智能体运行与认知', [
      'agent_loop_chat',
      'agent_loop_search',
      'agent_loop_scheduler',
      'agent_tool_events',
      'agent_context_trim',
      'agent_trace_group',
      'agent_daily_reflection',
      'agent_reflection_inject',
      'agent_daily_memory_maintenance',
    ]),
    FlagGroup('主动消息', [
      'proactive_naturalness_score',
      'proactive_user_rhythm',
    ]),
    FlagGroup('群聊小游戏', [
      'group_chat_games',
      'game_undercover',
      'game_truth_or_dare',
      'game_twenty_q',
      'game_werewolf',
      'game_liars_bar',
      'game_turtle_soup',
      'game_memory_bridge',
      'game_ai_autoplay',
    ]),
    FlagGroup('AI 自主生活', [
      'life_loop_enabled',
      'life_loop_visible',
      'life_loop_llm',
      'life_chat_driven_enabled',
      'life_home_worldmap_enabled',
    ]),
    FlagGroup('生命感增强', [
      'reply_delay_enabled',
      'spring_emotion_enabled',
      'life_share_enabled',
      'preoccupation_enabled',
    ]),
  ];

  // ── 每个键的中文名称 / 两行短说明 / 完整说明 ────────────────────
  static const Map<String, FlagMeta> _metas = {
    // 智能体运行与认知
    'agent_loop_chat': FlagMeta(
      title: '聊天主循环',
      short_: 'AI 回复走统一的智能体主循环：理解 → 记忆 → 规划 → 回答。',
      detail: '开启后每条聊天都经过统一 Runtime，注入世界认知与记忆后再回答，'
          '角色更连贯；关闭则回退到旧的直接生成链路。一般保持开启，'
          '仅在排查新链路问题时临时关闭。',
    ),
    'agent_loop_search': FlagMeta(
      title: '联网搜索循环',
      short_: '允许 AI 在需要时自动联网搜索，结果不足时再补查一次。',
      detail: '开启后 AI 可发起搜索、读取真实结果再回答，最多补查 1 次；'
          '关闭则退回单次生成、不联网。',
    ),
    'agent_loop_scheduler': FlagMeta(
      title: '主动任务循环',
      short_: '定时/主动触发的任务走统一智能体循环。',
      detail: '让到点触发的提醒、主动关心等经过统一 Runtime 并记录过程；'
          '关闭后主动任务走旧链路。',
    ),
    'agent_tool_events': FlagMeta(
      title: '工具联动织库',
      short_: 'AI 调用工具产生的关键事件，自动增量写入织库/记忆。',
      detail: '开启后工具执行结果会沉淀为记忆增量；关闭则工具执行不联动记忆。',
    ),
    'agent_context_trim': FlagMeta(
      title: '上下文按热度裁剪',
      short_: '低频角色少注入、高频角色全量注入，以节省 token。',
      detail: '低频角色缩小日摘要/织库等注入量，高频角色保持全量；'
          '关闭后所有角色统一全量注入，token 消耗更高。',
    ),
    'agent_trace_group': FlagMeta(
      title: '群聊过程记录',
      short_: '记录群聊中每个角色回应的决策过程（只写不读，用于排查）。',
      detail: '属于可观测日志，不影响回复内容；关闭后不再写群聊 trace。',
    ),
    'agent_daily_reflection': FlagMeta(
      title: '周期复盘',
      short_: '让 AI 周期性复盘近期经历（约每 7 天一次）。',
      detail: '复盘结果让角色言行更连贯；关闭后不再生成周期复盘。',
    ),
    'agent_reflection_inject': FlagMeta(
      title: '复盘注入主动消息',
      short_: '发主动消息时带上最近一次复盘，内容更贴合 AI 近况。',
      detail: '需配合「周期复盘」使用；关闭后主动消息不注入复盘。',
    ),
    'agent_daily_memory_maintenance': FlagMeta(
      title: '日终记忆维护',
      short_: '每天自动补日摘要、去重、补置顶摘要，保持记忆库整洁。',
      detail: '关闭后这些维护不再自动执行，长期可能积累重复记忆或缺失摘要。',
    ),

    // 主动消息
    'proactive_naturalness_score': FlagMeta(
      title: '主动消息自然度评分',
      short_: '低优先主动消息生成后评分，不自然就重试一次，仍差则不发。',
      detail: '用于减少生硬、打扰感的主动消息；关闭则主动消息原样发送。',
    ),
    'proactive_user_rhythm': FlagMeta(
      title: '用户作息学习',
      short_: '学习你的活跃时段，非活跃时间推迟低优先主动消息。',
      detail: '从聊天与主动日志推断作息；关闭则不区分时段，随时可能发。',
    ),

    // 群聊小游戏
    'group_chat_games': FlagMeta(
      title: '群聊游戏总开关',
      short_: '「小家 · 游戏机」整体入口与相关接口的总开关。',
      detail: '关闭后游戏入口与 API 都不展示，可整体回退游戏功能，不影响聊天。',
    ),
    'game_undercover': FlagMeta(
      title: '谁是卧底',
      short_: '多人轮流描述词语、投票找出卧底的桌游。',
      detail: '单个游戏的启用开关；关闭后该游戏不在可选列表。',
    ),
    'game_truth_or_dare': FlagMeta(
      title: '真心话大冒险',
      short_: '轮流选择真心话或大冒险的轻量游戏。',
      detail: '单个游戏的启用开关；关闭后不可选。',
    ),
    'game_twenty_q': FlagMeta(
      title: '猜词 20 问',
      short_: '用是非问句在限定轮数内猜出对方想的词。',
      detail: '单个游戏的启用开关；关闭后不可选。',
    ),
    'game_werewolf': FlagMeta(
      title: '狼人杀',
      short_: '夜晚行动 + 白天发言投票的多人桌游，含系统主持人。',
      detail: '单个游戏的启用开关；关闭后不可选。',
    ),
    'game_liars_bar': FlagMeta(
      title: '骗子酒馆',
      short_: '声明牌面、可质疑上家的心理博弈游戏。',
      detail: '单个游戏的启用开关；关闭后不可选。',
    ),
    'game_turtle_soup': FlagMeta(
      title: '海龟汤',
      short_: '通过是非提问，还原「汤面」背后真相，AI 当主持人。',
      detail: '单个游戏的启用开关；关闭后不可选。',
    ),
    'game_memory_bridge': FlagMeta(
      title: '游戏记忆桥接',
      short_: '在主记忆留一条游戏摘要指针，可回溯到独立游戏记忆库。',
      detail: '开启后「玩过什么」会在主记忆留痕并可调取游戏库详情；'
          '关闭则游戏过程只存在独立游戏库，不混入生活记忆。',
    ),
    'game_ai_autoplay': FlagMeta(
      title: 'AI 自动回合',
      short_: '轮到 AI 角色时自动行动，无需手动点下一步。',
      detail: '默认开以保证流程顺畅；关闭后每步 AI 行动需手动推进，主要用于调试。',
    ),

    // AI 自主生活
    'life_loop_enabled': FlagMeta(
      title: '生活循环主开关',
      short_: '约每 30 分钟让 AI 自主决策一次行为，形成自己的生活节奏。',
      detail: '关闭后角色的自主生活循环停止；你离线时也不再推进其生活。',
    ),
    'life_loop_visible': FlagMeta(
      title: '生活行为对外可见',
      short_: '允许自主生活产生面向你可见的输出（动态、分享等）。',
      detail: '关闭后生活行为只在后台记录，不向你展示。',
    ),
    'life_loop_llm': FlagMeta(
      title: '生活文案生成',
      short_: '允许用大模型写生活文案（每角色每日上限 2 次）。',
      detail: '让日记/动态更生动；关闭则只用模板规则文案以节省 token。',
    ),
    'life_chat_driven_enabled': FlagMeta(
      title: '聊天驱动生活',
      short_: '从聊天识别生活意图，联动改变 AI 的目标与活动。',
      detail: '例如你提到的事影响角色后续安排；关闭则聊天不联动生活循环。',
    ),
    'life_home_worldmap_enabled': FlagMeta(
      title: '小家大地图',
      short_: '「小家」中的世界大地图功能与相关自主行为。',
      detail: '关闭后小家不展示大地图。',
    ),

    // 生命感增强
    'reply_delay_enabled': FlagMeta(
      title: '动态回复延迟',
      short_: '按情境给回复加自然的短暂延迟（仅你主动发消息时生效）。',
      detail: '模拟真人思考/打字节奏；关闭则回复立即开始。',
    ),
    'spring_emotion_enabled': FlagMeta(
      title: '弹簧阻尼情绪',
      short_: '四维情绪 + 人格基线，情绪起伏后自然回落到基线。',
      detail: '让心情像真人一样波动而非跳变；关闭则用旧的简单情绪模型。',
    ),
    'life_share_enabled': FlagMeta(
      title: '活动自然分享',
      short_: '活动完成后在合适时机自然分享给你（带频率门控，防刷屏）。',
      detail: '关闭后 AI 不会主动分享刚完成的活动。',
    ),
    'preoccupation_enabled': FlagMeta(
      title: '心事微澜',
      short_: '让角色偶尔带着一点没说出口的小心事，更有牵挂感。',
      detail: '复用记忆子类型实现；关闭则无此效果。',
    ),
  };
}
