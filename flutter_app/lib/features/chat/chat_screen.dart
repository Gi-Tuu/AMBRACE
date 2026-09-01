// F7-b（2026-08-31）自 screens/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/message.dart';
import '../../models/character_state.dart';
import '../../providers/chat_provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../services/notification_service.dart';
import '../../global_keys.dart';
import '../../services/phone_perception_service.dart';
import '../../theme/aurora_tokens.dart';
import '../../theme/tokens.dart';
import '../../utils/stage_text.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/app_page_route.dart';
import '../../widgets/glass_bar.dart';
import '../../widgets/message_bubble.dart';
import '../../widgets/chat_time_separator.dart';
import '../../screens/character/character_detail_screen.dart';
import 'chat_message_media_actions.dart';
import 'chat_phone_actions.dart';
import 'chat_input_sections.dart';
import 'chat_screen_widgets.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}
class _ChatScreenState extends State<ChatScreen>
    with RouteAware, ChatPhoneActions, ChatMessageMediaActions {
  final _controller = TextEditingController();
  final _inputFocusNode = FocusNode();
  final _scrollController = ScrollController();
  bool _initialized = false;
  String _moodEmoji = '';
  int? _moodCharId;
  /// 更多功能面板（微信式内嵌，点 + 展开把输入框顶下去）
  bool _morePanelOpen = false;
  /// 切换按钮（发送按钮左侧）：GlobalKey 用于定位弹出小框
  final _switchKey = GlobalKey();

  /// 上次构建时消息列表长度（用于判断「新插入项」以只对其播放入场动画；-1 表示尚未构建过列表）。
  int _lastBuiltCount = -1;
  /// 消息列表是否已完成过一次「有内容」的构建（避免首帧装载历史消息时整屏播放入场动画）。
  bool _listBuilt = false;
  /// B3 回底按钮：是否贴近底部（贴底时不显示悬浮按钮；初始 true 保证首屏自动滚到底）
  bool _nearBottom = true;
  /// B3 回底按钮：不在底部时来了新消息（红点提示）
  bool _hasUnreadBelow = false;

  /// 真玻璃：底部输入栏实测高度（回传给消息列表做底部留白，避免被浮层遮挡）
  final ValueNotifier<double> _dockHeight = ValueNotifier(0);

  /// 待发送引用（长按气泡-引用设置；发送后清空）
  Map<String, dynamic>? _quote;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScrollChanged);
    // 输入内容变化不再整页 setState：发送按钮改用 ValueListenableBuilder 局部重建（见问题 2.1）
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final chat = context.watch<ChatProvider>();
    if (!_initialized) {
      _initialized = true;
      // B3 build 期隐患治理：startSession 会 notifyListeners，延迟到帧后执行，
      // 避免 didChangeDependencies 期间 markNeedsBuild 断言
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        final chat = context.read<ChatProvider>();
        if (chat.sessionId == null && chat.currentCharacter != null) {
          chat.setUserId(context.read<SettingsProvider>().userId);
          chat.startSession();
        }
      });
    }
    // 角色变化时（重新进入聊天页）刷新心情标识 + 聊天偏好（思考/调用能力开关）
    if (chat.currentCharacter?.id != _moodCharId) {
      _loadMood(chat.currentCharacter?.id);
      _loadChatPrefs(chat.currentCharacter?.id);
    }
    final route = ModalRoute.of(context);
    if (route != null) appRouteObserver.subscribe(this, route);
  }

  // 角色聊天偏好：思考过程挡位/调用能力显示开关（来自角色设置-隐私组）
  int _reasoningLevel = 0;
  bool _showTools = false;

  /// 加载角色聊天偏好（思考过程/调用能力显示开关；失败按关闭处理不阻塞）
  Future<void> _loadChatPrefs(int? characterId) async {
    if (characterId == null) {
      if (mounted) setState(() { _reasoningLevel = 0; _showTools = false; });
      return;
    }
    try {
      final settings = await ApiClient().getSchedulerSettings(characterId);
      if (!mounted) return;
      setState(() {
        _reasoningLevel = settings['reasoning_level'] as int? ?? 0;
        _showTools = settings['show_tools_enabled'] as bool? ?? false;
      });
    } catch (_) {
      if (mounted) setState(() { _reasoningLevel = 0; _showTools = false; });
    }
  }

  /// 加载角色八维状态 → 映射为心情 emoji（失败静默，不阻塞聊天）
  Future<void> _loadMood(int? characterId) async {
    if (characterId == null) {
      if (mounted) setState(() { _moodEmoji = ''; _moodCharId = null; });
      return;
    }
    _moodCharId = characterId;
    try {
      // 开关（角色设置-状态-聊天页心情标识）：关闭则不显示；接口失败按开启处理不阻塞
      var badgeOn = true;
      try {
        final settings = await ApiClient().getSchedulerSettings(characterId);
        badgeOn = settings['mood_badge_enabled'] as bool? ?? true;
      } catch (_) {}
      if (!badgeOn) {
        if (mounted && _moodCharId == characterId) setState(() => _moodEmoji = '');
        return;
      }
      final st = await ApiClient().getCharacterStates(characterId);
      if (!mounted || _moodCharId != characterId) return;
      setState(() => _moodEmoji = _moodEmojiFor(st));
    } catch (_) {
      // 静默：接口失败不显示标识
    }
  }

  static String _moodEmojiFor(CharacterState st) {
    if (st.anger >= 70) return '😠';
    if (st.fatigue >= 75) return '😪';
    if (st.mood >= 70) return '😄';
    if (st.mood >= 55) return '🙂';
    if (st.mood >= 40) return '😐';
    if (st.mood >= 25) return '😔';
    return '😢';
  }

  @override
  void didPush() => _reportActiveScreenDeferred();

  @override
  void didPop() {
    // 退出聊天页兜底恢复可弹（下层页面会随后覆盖为自身状态）
    _clearActiveScreenDeferred();
  }

  @override
  void didPopNext() {
    _reportActiveScreenDeferred();
    // 从角色设置/详情页返回时刷新聊天偏好（思考过程/调用能力开关可能刚被修改）
    _loadChatPrefs(context.read<ChatProvider>().currentCharacter?.id);
  }

  @override
  void didPushNext() {
    // 聊天页被其他页面覆盖（如角色详情/记忆本）→ 恢复可弹
    _clearActiveScreenDeferred();
  }

  /// B3 build 期隐患治理：setActiveScreen 会通知所有监听者（列表页 setState），
  /// RouteObserver 回调可能发生在 build 期间，统一延迟到帧后执行。
  void _reportActiveScreenDeferred() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final chat = context.read<ChatProvider>();
      NotificationService().setActiveScreen(
        ActiveScreen.chat,
        characterId: chat.currentCharacter?.id,
      );
    });
  }

  void _clearActiveScreenDeferred() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      NotificationService().setActiveScreen(ActiveScreen.other);
    });
  }

  Future<void> _sendMessage() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    final chat = context.read<ChatProvider>();
    // 手机感知：用户问“你在干嘛/你在看什么”等时，先采集上传快照再发送（开关未开启时静默跳过）
    final wantsPerception = PhonePerceptionService.hasPerceptionIntent(text);
    final wantsAction = PhonePerceptionService.hasActionIntent(text);
    String? sendText;
    if (wantsPerception || wantsAction) {
      await PhonePerceptionService.collectAndUpload();
      // Phase 3 模拟操作：动作意图 + 已授权时执行（序列模板/节点匹配/选择 → 授权 → 执行 → 结果回传）
      if (wantsAction && await PhonePerceptionService.isActionsEnabled()) {
        sendText = await runPhoneAction(text);
      }
    }
    // 序列已代为输入/发送时（如“帮我回‘好的’”），发送目标内容而非指令文本
    chat.sendMessage(sendText ?? text, quote: _quote);
    if (mounted) {
      _controller.clear();
      if (_quote != null) setState(() => _quote = null);
    }
  }

  /// 被引用消息是否已删除（会话消息全量加载，列表内找不到即视为已删）
  bool _isQuoteDeleted(ChatMessage msg, List<ChatMessage> all) {
    final quote = msg.quoteMeta;
    if (quote == null) return false;
    final id = (quote['message_id'] as num?)?.toInt();
    if (id == null) return true;
    return !all.any((m) => m.id == id);
  }

  /// 长按气泡-引用回写（ChatMessageMediaActions 抽象入口）。
  @override
  void applyQuote(covariant ChatMessage msg) {
    setState(() {
      _quote = {
        'message_id': msg.id,
        'sender': msg.isUser ? 'user' : 'ai',
        'content': StageText.excerpt(msg.content, max: 100),
      };
    });
  }
  /// 退出输入态：收键盘 + 关更多面板（不清空已输入文字）
  void _exitInputState() {
    FocusManager.instance.primaryFocus?.unfocus();
    if (_morePanelOpen && mounted) setState(() => _morePanelOpen = false);
  }

  // "+" 键：展开/收起更多功能面板。
  // 关键：收起时【绝不重新请求焦点】，避免再点一次键盘又升起。
  void _toggleMorePanel() {
    final willOpen = !_morePanelOpen;
    setState(() => _morePanelOpen = willOpen);
    FocusManager.instance.primaryFocus?.unfocus();
  }

  /// 切换小框（气泡式，类似长按气泡菜单）：连续发送 / 语音发送 / 表情
  void _showSwitchMenu() {
    _exitInputState(); // 先收键盘/关更多面板；showMenu 本身已支持点外部关闭
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    final box = _switchKey.currentContext?.findRenderObject() as RenderBox?;
    if (box == null) return;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox?;
    final size = overlay?.size ?? const Size(0, 0);
    final pos = box.localToGlobal(Offset.zero);
    showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(
        pos.dx,
        pos.dy,
        size.width - pos.dx,
        size.height - pos.dy,
      ),
      items: [
        PopupMenuItem(
          value: 'batch',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.playlist_add, size: 18, color: chat.batchMode ? Colors.orange : null),
            const SizedBox(width: 8),
            Text(l10n.chatContinuous),
            if (chat.batchMode) ...[
              const SizedBox(width: 8),
              Icon(Icons.check, size: 16, color: Theme.of(context).colorScheme.primary),
            ],
          ]),
        ),
        PopupMenuItem(
          value: 'voice',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.mic_none, size: 18),
            const SizedBox(width: 8),
            Text(l10n.chatVoiceSend),
          ]),
        ),
        PopupMenuItem(
          value: 'emoji',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.emoji_emotions_outlined, size: 18),
            const SizedBox(width: 8),
            Text(l10n.chatEmoji),
          ]),
        ),
      ],
    ).then((value) {
      if (value == null || !mounted) return;
      switch (value) {
        case 'batch':
          chat.toggleBatchMode();
          break;
        case 'voice':
          showVoiceRecorder();
          break;
        case 'emoji':
          showEmojiPanel();
          break;
      }
    });
  }


  @override
  void dispose() {
    appRouteObserver.unsubscribe(this);
    _controller.dispose();
    _inputFocusNode.dispose();
    _scrollController.removeListener(_onScrollChanged);
    _scrollController.dispose();
    _dockHeight.dispose();
    super.dispose();
  }

  /// 滚动位置监听：更新回底按钮可见性与红点
  void _onScrollChanged() {
    if (!_scrollController.hasClients) return;
    final near =
        _scrollController.position.maxScrollExtent - _scrollController.offset < 120;
    if (near != _nearBottom && mounted) {
      setState(() {
        _nearBottom = near;
        if (near) _hasUnreadBelow = false;
      });
    }
  }

  /// 回底按钮点击：滚到底部并清除红点
  void _jumpToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.animateTo(
      _scrollController.position.maxScrollExtent,
      duration: AppMotion.normal,
      curve: AppMotion.emphasized,
    );
    if (mounted) setState(() => _hasUnreadBelow = false);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.watch<ChatProvider>();
    final char = chat.currentCharacter;

    // 自动滚动到底部 + 记录已构建消息数（供新插入项入场动画判定）；
    // 仅在本次确实渲染了消息列表时才更新 _listBuilt/_lastBuiltCount，避免首帧装载历史消息整屏播放入场动画。
    // B3：仅贴近底部时才自动滚底（用户上滑阅读历史时不打断）；不在底部来了新消息 → 回底按钮红点。
    final listRendered = char != null && !(chat.isLoading && chat.messages.isEmpty);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (listRendered) {
        final hasNew = _lastBuiltCount >= 0 && chat.messages.length > _lastBuiltCount;
        if (hasNew && !_nearBottom && !_hasUnreadBelow && mounted) {
          setState(() => _hasUnreadBelow = true);
        }
        _listBuilt = true;
        _lastBuiltCount = chat.messages.length;
      }
      if (_scrollController.hasClients && _nearBottom) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 100),
          curve: Curves.easeOut,
        );
      }
    });
    // 真玻璃：消息从顶栏后穿过，需要为列表补一个顶栏高度的顶部 padding
    final double topBarInset =
        MediaQuery.of(context).padding.top + kToolbarHeight;

    return Scaffold(
      // 真玻璃：让 body 延伸到 AppBar 后面，flexibleSpace 的模糊才能糊化滚到其下的消息
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        // Aurora 真玻璃顶栏：背景透明，毛玻璃交给 flexibleSpace 的 GlassBar
        backgroundColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        flexibleSpace: const RepaintBoundary(
          child: GlassBar(
            border: GlassBarBorder.bottom,
            blur: AppGlass.blurLight,
            child: SizedBox.expand(),
          ),
        ),
        title: Row(
          children: [
            GestureDetector(
              onTap: () {
                if (chat.sessionId != null && char != null) {
                  Navigator.push(
                    context,
                    AppPageRoute(
                      builder: (_) => CharacterDetailScreen(
                        character: char,
                        sessionId: chat.sessionId!,
                      ),
                    ),
                  );
                }
              },
              child: AIAvatar(name: char?.name ?? 'AI', size: 32, imageUrl: char?.avatarUrl),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Text(char?.name ?? l10n.selectFriend),
                  if (_moodEmoji.isNotEmpty) ...[
                    const SizedBox(width: 4),
                    Text(_moodEmoji, style: const TextStyle(fontSize: 16)),
                  ],
                ]),
                if (chat.isTyping)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('${char?.name ?? ''} ${l10n.typing}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 12, color: Colors.grey)),
                      const SizedBox(width: 3),
                      const TypingIndicator(),
                    ],
                  ),
              ],
            ),
          ],
        ),
        actions: [
          if (char != null)
            IconButton(
              tooltip: l10n.eventClockTitle,
              icon: const Icon(Icons.timer_outlined, size: 22),
              onPressed: () => showEventClockDialog(char.id),
            ),
        ],
      ),
      body: char == null
          ? Center(child: Text(l10n.chooseFriendFirst))
          : Stack(
              children: [
                // 消息列表：铺满整个 body（从顶栏后 / 输入栏后穿过，供真玻璃糊化）
                Positioned.fill(
                  child: (chat.isLoading && chat.messages.isEmpty)
                      ? const Center(child: CircularProgressIndicator())
                      : Stack(
                          children: [
                            // 点空白处退出输入态/关面板（不拦截气泡自身手势）
                            Positioned.fill(
                              child: GestureDetector(
                                behavior: HitTestBehavior.translucent,
                                onTap: _exitInputState,
                                child: Stack(
                                  children: [
                                    // 顶部让出顶栏、底部让出输入栏（输入栏高度实测）
                                    ValueListenableBuilder<double>(
                                      valueListenable: _dockHeight,
                                      builder: (context, dockH, _) => RepaintBoundary(
                                        child: ListView.builder(
                                          controller: _scrollController,
                                          padding: EdgeInsets.only(
                                            left: AppSpacing.sm,
                                            right: AppSpacing.sm,
                                            top: topBarInset,
                                            bottom: dockH + AppSpacing.sm,
                                          ),
                                          itemCount: chat.messages.length,
                                          itemBuilder: (context, index) {
                                            final msg = chat.messages[index];
                                            // 系统提示（如冷战"对方没有回应"）：居中灰色小字，不走气泡
                                            if (msg.senderType == 'system') {
                                              return Padding(
                                                padding: const EdgeInsets.symmetric(vertical: 8),
                                                child: Center(
                                                  child: Text(
                                                    msg.content,
                                                    style: TextStyle(
                                                      fontSize: 12,
                                                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                                                    ),
                                                    textAlign: TextAlign.center,
                                                  ),
                                                ),
                                              );
                                            }
                                            final isLastAi = msg.isAI && index == chat.messages.length - 1;
                                            // 连续消息合并时间：相邻同发送者且 5 分钟内只显示一次时间
                                            final prev = index > 0 ? chat.messages[index - 1] : null;
                                            final showTime = prev == null ||
                                                prev.senderType != msg.senderType ||
                                                !withinMinutes(prev.createdAt, msg.createdAt, 5);
                                            final isNewItem = _listBuilt && index >= _lastBuiltCount;
                                            final isStreamingMsg = msg.isAI && msg.isLocal && chat.isStreaming;
                                            // B3：连续 AI 消息仅组首条显示头像
                                            final prevIsAI = prev != null && prev.isAI;
                                            return Column(
                                              crossAxisAlignment: CrossAxisAlignment.stretch,
                                              children: [
                                                // #2 时间回到气泡内下方（连续消息仅组首显示）
                                                MessageEntrance(
                                                  animate: isNewItem,
                                                  child: MessageBubble(
                                                    message: msg.content,
                                                    isUser: msg.isUser,
                                                    time: msg.createdAt,
                                                    showTime: showTime && msg.createdAt.isNotEmpty,
                                                    imageUrl: msg.imageUrl,
                                                    fileMeta: msg.fileMeta,
                                                    voiceMeta: msg.voiceMeta,
                                                    ttsMeta: msg.ttsMeta,
                                                    reasoning: msg.reasoning,
                                                    tools: msg.tools,
                                                    toolResults: msg.toolResults,
                                                    statusUpdate: msg.statusUpdate,
                                                    showReasoning: _reasoningLevel > 0,
                                                    showTools: _showTools,
                                                    isStreaming: isStreamingMsg,
                                                    showAiAvatar: !(msg.isAI && prevIsAI),
                                                    serverUrl: ApiClient().baseUrl,
                                                    aiAvatarUrl: char.avatarUrl,
                                                    userAvatarUrl: context.read<SettingsProvider>().avatarUrl,
                                                    quoteMeta: msg.quoteMeta,
                                                    quoteDeleted: _isQuoteDeleted(msg, chat.messages),
                                                    onContinue: isLastAi ? () => chat.continueChat() : null,
                                                    onMenu: (offset) => showBubbleMenu(offset, msg, index),
                                                  ),
                                                ),
                                              ],
                                            );
                                          },
                                        ),
                                      ),
                                    ),
                                    // B3 回底按钮：不在底部时右下角悬浮；底部抬到输入栏之上（真玻璃浮层）
                                    if (!_nearBottom)
                                      ValueListenableBuilder<double>(
                                        valueListenable: _dockHeight,
                                        builder: (context, dockH, _) => Positioned(
                                          right: 12,
                                          bottom: dockH + 12,
                                          child: BackToBottomButton(
                                            hasUnread: _hasUnreadBelow,
                                            onTap: _jumpToBottom,
                                          ),
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                ),
                // 输入栏：浮在消息之上（其自身 BackdropFilter 即真玻璃）；
                // _MeasureSize 把实测高度回传，给消息列表做底部留白
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: MeasureSize(
                    onChange: (size) {
                      final h = size.height;
                      if ((_dockHeight.value - h).abs() > 0.5) {
                        _dockHeight.value = h;
                      }
                    },
                    child: Builder(builder: (context) {
                  final scheme = Theme.of(context).colorScheme;
                  final isDark = Theme.of(context).brightness == Brightness.dark;
                  // #1/#12：仅「极光毛玻璃」皮肤半透明+高斯模糊；其余皮肤输入栏与页面同色，
                  // 不再出现一块悬浮的白色遮罩。
                  final isGlass = context.read<SettingsProvider>().skinId == 'glass';
                  final useBlur = isGlass;
                  final borderColor = isDark
                      ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                      : Colors.black.withValues(alpha: AppGlass.borderAlpha);
                  final reduceBlur = maybeReduceBlur(context);
                  final sigma = AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: reduceBlur);

                  // 把原来的 Row（+ / TextField / 切换 / 发送）原样保留为 controlsRow
                  final Widget controlsRow = Row(
                    children: [
                        // 语音通话入口（Phase A 已搁置 2026-08-12：真机播放/实时性未达标，恢复需 Phase C）
                        // IconButton(
                        //   icon: const Icon(Icons.phone_outlined),
                        //   tooltip: '语音通话',
                        //   onPressed: () { ... },
                        // ),
                        // "+" 键：微信式更多功能（展开面板把输入框顶下去）
                        IconButton(
                          icon: Icon(
                            Icons.add_circle_outline,
                            color: _morePanelOpen ? Theme.of(context).colorScheme.primary : null,
                          ),
                          tooltip: l10n.moreFunctions,
                          onPressed: _toggleMorePanel,
                        ),
                        Expanded(
                          child: TextField(
                            controller: _controller,
                            focusNode: _inputFocusNode,
                            // 点输入框以外任意位置 → 立即退出输入态
                            onTapOutside: (event) => _exitInputState(),
                            maxLines: 5,
                            minLines: 1,
                            keyboardType: TextInputType.multiline,
                            decoration: InputDecoration(
                              hintText: chat.batchMode ? l10n.inputHintBatch : l10n.inputHint,
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                              border: OutlineInputBorder(
                                borderRadius: BorderRadius.circular(22),
                                borderSide: BorderSide.none,
                              ),
                              contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
                            ),
                            onSubmitted: (_) => _sendMessage(),
                            onTap: () {
                              if (_morePanelOpen) setState(() => _morePanelOpen = false);
                            },
                          ),
                        ),
                        const SizedBox(width: 4),
                        // 第 3 位：连续发送开启时=「提交收集」模式键（顶替「切换」）；
                        //         未开启时=原「切换」气泡菜单（连续/语音/表情）。
                        if (chat.batchMode)
                          // 连续模式：点按发送已收集消息并退出；pending=0 时仅退出连续模式
                          IconButton(
                            tooltip: l10n.chatContinuous,
                            onPressed: chat.toggleBatchMode,
                            icon: Badge(
                              isLabelVisible: chat.pendingBatchCount > 0,
                              label: Text("${chat.pendingBatchCount}"),
                              child: Icon(
                                Icons.playlist_add_check,
                                color: scheme.tertiary,
                              ),
                            ),
                          )
                        else
                          IconButton(
                            key: _switchKey,
                            tooltip: l10n.switchMode,
                            onPressed: _showSwitchMenu,
                            icon: const Icon(Icons.swap_horiz),
                          ),
                        // 第 4 位：发送键常驻。连续模式下点按=把当前草稿收集为一条（内部换行原样保留），
                        // 不再被替换成模式键；空输入灰色禁用，逻辑与非连续模式完全一致。
                        ValueListenableBuilder<TextEditingValue>(
                          valueListenable: _controller,
                          builder: (context, val, _) {
                            final hasText = val.text.trim().isNotEmpty;
                            // B8（2026-09-01 审查）：流式进行中禁用发送（连续发送模式除外）
                            final canSend = hasText && (!chat.isStreaming || chat.batchMode);
                            return PressScale(
                              reduceMotion: MediaQuery.disableAnimationsOf(context) ||
                                  maybeReduceMotion(context),
                              child: IconButton(
                                icon: Icon(
                                  Icons.send,
                                  color: canSend
                                      ? scheme.onPrimary
                                      : scheme.onSurfaceVariant,
                                ),
                                style: IconButton.styleFrom(
                                  backgroundColor: canSend
                                      ? scheme.primary
                                      : scheme.surfaceContainerHighest
                                          .withValues(alpha: 0.6),
                                ),
                                onPressed: canSend ? _sendMessage : null,
                              ),
                            );
                          },
                        ),
                    ],
                  );

                  // 圆角 dock：颜色只落在这一行上，不再整条全宽铺色 → 消除白条
                  final Widget dock = Container(
                    margin: const EdgeInsets.symmetric(horizontal: 4),
                    padding: const EdgeInsets.fromLTRB(2, 4, 4, 4),
                    decoration: BoxDecoration(
                      // 玻璃皮肤：半透明承托（外层 BackdropFilter 负责高斯模糊）
                      // 其余皮肤：scheme.surface 恢复「输入行白卡片」；
                      //          外层 bar 仍透明，所以输入框上方不会再有横白条。
                      color: isGlass
                          ? (isDark
                              ? Colors.black.withValues(alpha: 0.18)
                              : Colors.white.withValues(alpha: 0.50))
                          : scheme.surface,
                      borderRadius: BorderRadius.circular(28),
                      border: isGlass
                          ? Border.all(color: borderColor, width: 0.5)
                          : Border.all(
                              // 非玻璃皮肤给一道极淡分隔，卡片更立体（可删）
                              color: scheme.outlineVariant.withValues(alpha: 0.25),
                              width: 0.5,
                            ),
                    ),
                    child: controlsRow,
                  );

                  Widget bar = Container(
                    color: Colors.transparent, // 关键：外层不再铺色，输入框上方不再有白条
                    padding: const EdgeInsets.fromLTRB(4, 4, 4, 4),
                    child: SafeArea(
                      top: false,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (chat.pendingPermission != null)
                  PermissionCard(
                    scopeLabel: chat.pendingPermission!.scopeLabel,
                    prompt: chat.pendingPermission!.prompt,
                    onDeny: () => chat.denyPendingPermission(),
                    onAllow: () => chat.approvePendingPermission(),
                  ),
                          if (_quote != null)
                  QuoteBar(
                    content: _quote!['content'] as String? ?? '',
                    senderIsUser: _quote!['sender'] == 'user',
                    onClose: () => setState(() => _quote = null),
                  ),
                          if (_morePanelOpen)
                  MorePanel(
                    onPickImage: () {
                      setState(() => _morePanelOpen = false);
                      pickAndUploadImage();
                    },
                    onPickFile: () {
                      setState(() => _morePanelOpen = false);
                      pickAndSendFile();
                    },
                    onVoiceCall: () {
                      setState(() => _morePanelOpen = false);
                      startVoiceCall();
                    },
                  ),
                          dock,
                        ],
                      ),
                    ),
                  );
                  if (!useBlur) return bar;
                  // 问题 2：模糊层用 RepaintBoundary 隔离，键盘动画不连带消息列表重绘
                  return RepaintBoundary(
                    child: ClipRect(
                      child: BackdropFilter(
                        filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
                        child: bar,
                      ),
                    ),
                  );
                    }),
                  ),
                ),
              ],
            ),
    );
  }
}