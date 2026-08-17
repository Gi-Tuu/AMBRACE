import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/chat_provider.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/message_bubble.dart';
import 'package:flutter/services.dart';
import '../../models/message.dart';
import '../../utils/stage_text.dart';
import 'dart:io';
import 'package:image_picker/image_picker.dart';
import 'package:file_picker/file_picker.dart';
import 'package:record/record.dart';
import '../../services/api_client.dart';
import '../../services/api/emojis_api.dart';
import '../../global_keys.dart';
import '../../services/notification_service.dart';
import '../../services/phone_perception_service.dart';
import '../../services/home_tab_controller.dart';
import '../../widgets/ai_avatar.dart';
import '../../models/character_state.dart';
import '../character/character_detail_screen.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with RouteAware {
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

  /// 待发送引用（长按气泡-引用设置；发送后清空）
  Map<String, dynamic>? _quote;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final chat = context.watch<ChatProvider>();
    if (!_initialized) {
      _initialized = true;
      if (chat.sessionId == null && chat.currentCharacter != null) {
        chat.setUserId(context.read<SettingsProvider>().userId);
        chat.startSession();
      }
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
  void didPush() => _reportActiveScreen();

  @override
  void didPop() {
    // 退出聊天页兜底恢复可弹（下层页面会随后覆盖为自身状态）
    NotificationService().setActiveScreen(ActiveScreen.other);
  }

  @override
  void didPopNext() {
    _reportActiveScreen();
    // 从角色设置/详情页返回时刷新聊天偏好（思考过程/调用能力开关可能刚被修改）
    _loadChatPrefs(context.read<ChatProvider>().currentCharacter?.id);
  }

  @override
  void didPushNext() {
    // 聊天页被其他页面覆盖（如角色详情/记忆本）→ 恢复可弹
    NotificationService().setActiveScreen(ActiveScreen.other);
  }

  void _reportActiveScreen() {
    final chat = context.read<ChatProvider>();
    NotificationService().setActiveScreen(ActiveScreen.chat, characterId: chat.currentCharacter?.id);
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
        sendText = await _runPhoneAction(text);
      }
    }
    // 序列已代为输入/发送时（如“帮我回‘好的’”），发送目标内容而非指令文本
    chat.sendMessage(sendText ?? text, quote: _quote);
    if (mounted) {
      _controller.clear();
      if (_quote != null) setState(() => _quote = null);
    }
  }

  /// Phase 3：AI 帮用户操作当前屏幕。返回“代替发送的文本”（序列已代为输入时），否则 null。
  /// 3.4a：先尝试序列模板（帮我回/发/发布/点赞/播放），失败或不匹配再走单步节点选择。
  Future<String?> _runPhoneAction(String userText) async {
    final l10n = AppLocalizations.of(context)!;
    if (!mounted) return null;
    final messenger = ScaffoldMessenger.of(context);
    // P1 工作流（2026-08-14）：用户自建序列（可含 Shizuku 系统级步骤）优先匹配
    final wf = await PhonePerceptionService.matchWorkflow(userText);
    if (wf != null && mounted) {
      final wfSteps = (wf['steps'] as List? ?? []).cast<Map>();
      if (wfSteps.isNotEmpty) {
        final wfName = wf['name'] as String? ?? l10n.wfDefaultName;
        final okRun = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.chatRunWf(wfName)),
            content: Text(l10n.chatWfSteps(wfSteps.length)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
              FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.input)),
            ],
          ),
        );
        if (okRun == true && mounted) {
          final wfResults = await PhonePerceptionService.executeActionSequence(wfSteps);
          final wfOk = wfResults.every((r) => r['ok'] == true);
          final wfSummary = wfResults
              .map((r) => l10n.chatWfStep(r['step'], r['ok'] == true ? '✓' : '✗', r['message'] ?? ''))
              .join('；');
          await PhonePerceptionService.uploadActionResult('workflow', wfName, wfOk, wfSummary);
          if (mounted) {
            messenger.showSnackBar(SnackBar(
              content: Text(wfOk ? l10n.chatWfDone(wfSummary) : l10n.chatWfInterrupted(wfSummary), maxLines: 3),
            ));
          }
        }
        return null;
      }
    }
    final tree = await PhonePerceptionService.getNodeTree();
    if (!mounted) return null;
    final serviceEnabled = tree["serviceEnabled"] as bool? ?? false;
    if (!serviceEnabled) {
      messenger.showSnackBar(SnackBar(content: Text(l10n.chatNoAccessibility)));
      return null;
    }

    // === 序列模板（3.4a） ===
    final template = PhonePerceptionService.parseActionTemplate(userText);
    if (template != null) {
      final steps = (template["steps"] as List? ?? []).cast<Map>();
      if (steps.isNotEmpty) {
        if (!PhonePerceptionService.autoAllowed) {
          final mode = await _showSequenceAuthDialog(template);
          if (!mounted) return null;
          if (mode == "reject" || mode == null) return null;
          if (mode == "minute") PhonePerceptionService.allowForMinute();
        }
        // 朋友圈相关模板：先切回朋友圈 tab 再执行（自家 app 内导航，轻度干涉不跨 app）
        final seqType = template["type"] as String? ?? "";
        if (seqType == "publish" || seqType == "like") {
          HomeTabController.switchTo(1);
          final nav = Navigator.of(context);
          if (nav.canPop()) nav.pop();
          await Future.delayed(const Duration(milliseconds: 1000));
        }
        final results = await PhonePerceptionService.executeActionSequence(steps);
        final allOk = results.every((r) => r["ok"] == true);
        final summary = results
            .map((r) => l10n.chatWfStep(r["step"], r["ok"] ? "✓" : "✗", r["message"]))
            .join("；");
        await PhonePerceptionService.uploadActionResult(
          "sequence",
          template["type"] as String? ?? "",
          allOk,
          summary,
        );
        messenger.showSnackBar(SnackBar(
          content: Text(allOk ? l10n.chatSeqDone(summary) : l10n.chatSeqInterrupted(summary), maxLines: 3),
        ));
        // 回复模板：文本已写入本 app 输入框，返回内容由发送流程代为发送（避免再点发送导致重复）
        return template["sendText"] as String?;
      }
    }

    final nodes = (tree["nodes"] as List? ?? []).cast<Map<dynamic, dynamic>>();
    final wantsLong = userText.contains("长按");
    final wantsInput = userText.contains("输入") ||
        userText.contains("回复") ||
        (userText.contains("发送") && userText.contains("文字"));

    // 目标节点：优先匹配用户消息里出现的节点文本（如“帮我点发送”→节点“发送”）
    Map<dynamic, dynamic>? target;
    if (!wantsInput) {
      for (final n in nodes) {
        final t = (n["text"] as String? ?? "").trim();
        if (t.isNotEmpty && userText.contains(t)) {
          target = n;
          break;
        }
      }
    }
    if (target == null && nodes.isNotEmpty) {
      final picked = await showModalBottomSheet<Map<dynamic, dynamic>>(
        context: context,
        builder: (ctx) => SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text(l10n.chatPickTarget,
                    style: const TextStyle(fontWeight: FontWeight.bold)),
              ),
              for (final n in nodes.take(30))
                ListTile(
                  dense: true,
                  title: Text(n["text"]?.toString() ?? ""),
                  subtitle: Text(n["editable"] == true ? l10n.nodeInput : l10n.nodeClickable),
                  onTap: () => Navigator.pop(ctx, n),
                ),
            ],
          ),
        ),
      );
      if (!mounted) return null;
      if (picked == null) return null;
      target = picked;
    }
    if (!mounted) return null;
    if (target == null) {
      messenger.showSnackBar(SnackBar(content: Text(l10n.chatNoNodes)));
      return null;
    }

    final targetText = (target["text"] as String? ?? "").trim();
    final String action;
    final String actionLabel;
    if (wantsInput) {
      action = "set_text";
      actionLabel = targetText.isEmpty ? "输入框" : targetText;
    } else if (wantsLong) {
      action = "long_click";
      actionLabel = targetText;
    } else {
      action = "click";
      actionLabel = targetText;
    }

    // 授权：允许本次 / 允许1分钟 / 拒绝（1分钟内已允许则跳过弹窗）
    if (!PhonePerceptionService.autoAllowed) {
      final mode = await _showActionAuthDialog(actionLabel, action == "set_text");
      if (!mounted) return null;
      if (mode == "reject" || mode == null) return null;
      if (mode == "minute") PhonePerceptionService.allowForMinute();
    }

    // 输入文本时额外询问内容（≤50 字）
    String? inputText;
    if (action == "set_text") {
      inputText = await _askInputText(actionLabel);
      if (!mounted) return null;
      if (inputText == null) return null;
    }

    final Map<dynamic, dynamic> res;
    if (action == "set_text") {
      res = await PhonePerceptionService.setTextOnFocus(inputText!);
    } else {
      res = await PhonePerceptionService.performAction(action, actionLabel);
    }
    final ok = res["ok"] as bool? ?? false;
    final msg = res["message"] as String? ?? l10n.chatOpDone;
    // 结果回传服务器（source=action_result），供聊天上下文引用与动作日志落库
    await PhonePerceptionService.uploadActionResult(
      action,
      actionLabel.isEmpty ? "目标" : actionLabel,
      ok,
      msg,
    );
    if (mounted) {
      messenger.showSnackBar(SnackBar(content: Text(msg)));
    }
    return null;
  }

  /// 序列确认弹窗：展示每一步 + 干涉档位说明（轻度干涉默认）
  Future<String?> _showSequenceAuthDialog(Map<String, dynamic> template) async {
    final l10n = AppLocalizations.of(context)!;
    final steps = (template["steps"] as List? ?? []).cast<Map>();
    final typeLabel = switch (template["type"]) {
      "reply" => l10n.seqReply,
      "publish" => l10n.seqPublish,
      "like" => l10n.seqLike,
      "play" => l10n.seqPlay,
      _ => l10n.seqCombo,
    };
    final stepLines = steps.map((s) {
      if (s["action"] == "set_text") {
        return l10n.seqInputLine(s["text"]);
      }
      final verb = s["action"] == "long_click" ? l10n.seqLongClick : l10n.seqClick;
      return l10n.seqClickLine(verb, s["target"]);
    }).join("\n");
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatSeqTitle),
        content: Text(
          l10n.chatSeqDesc(typeLabel, stepLines, (template["type"] == "publish" || template["type"] == "like") ? l10n.chatSeqAutoNote : ""),
          style: const TextStyle(fontSize: 13, height: 1.5),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, "reject"), child: Text(l10n.reject)),
          TextButton(onPressed: () => Navigator.pop(ctx, "once"), child: Text(l10n.allowOnce)),
          FilledButton(onPressed: () => Navigator.pop(ctx, "minute"), child: Text(l10n.allowMinute)),
        ],
      ),
    );
  }

  Future<String?> _showActionAuthDialog(String targetLabel, bool isInput) async {
    final l10n = AppLocalizations.of(context)!;
    final op = isInput ? l10n.chatOpInput : (targetLabel.isEmpty ? l10n.chatOpDefault : l10n.chatOpTarget(targetLabel));
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatOpTitle),
        content: Text(l10n.chatOpDesc(op)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, "reject"), child: Text(l10n.reject)),
          TextButton(onPressed: () => Navigator.pop(ctx, "once"), child: Text(l10n.allowOnce)),
          FilledButton(onPressed: () => Navigator.pop(ctx, "minute"), child: Text(l10n.allowMinute)),
        ],
      ),
    );
  }

  Future<String?> _askInputText(String targetLabel) async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatInputTitle),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 50,
          decoration: InputDecoration(
            hintText: targetLabel.isEmpty ? l10n.chatInputHint : l10n.chatInputHintTarget(targetLabel),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.input)),
        ],
      ),
    );
    if (ok != true) return null;
    final t = ctrl.text.trim();
    return t.isEmpty ? null : t;
  }


  Future<void> _deleteMessage(int messageId) async {
    try {
      await context.read<ChatProvider>().deleteMessage(messageId);
    } catch (_) {}
  }

  /// 事件时钟：展示未到期的定时承诺，允许用户删除（2026-08-15）
  Future<void> _showEventClockDialog(int characterId) async {
    final l10n = AppLocalizations.of(context)!;
    List<Map<String, dynamic>> items = [];
    String? error;
    try {
      items = await ApiClient().listTimers(characterId);
    } catch (e) {
      error = '$e';
    }
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.eventClockTitle),
        content: SizedBox(
          width: double.maxFinite,
          child: error != null
              ? Text('${l10n.loadFailed}: $error', style: const TextStyle(fontSize: 13, color: Colors.grey))
              : items.isEmpty
                  ? Text(l10n.eventClockEmpty, style: const TextStyle(fontSize: 13, color: Colors.grey))
                  : Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(l10n.eventClockHint, style: const TextStyle(fontSize: 12, color: Colors.grey)),
                        const SizedBox(height: 8),
                        Flexible(
                          child: ListView.builder(
                            shrinkWrap: true,
                            itemCount: items.length,
                            itemBuilder: (_, i) {
                              final it = items[i];
                              final owner = it['owner'] == 'user' ? l10n.userPromised : l10n.aiPromised;
                              final hint = it['content_hint'] as String? ?? l10n.doSomething;
                              final left = '${it['left_minutes']}${l10n.minutesLater}（${it['due_at']}）';
                              return ListTile(
                                dense: true,
                                contentPadding: EdgeInsets.zero,
                                leading: Icon(
                                  it['owner'] == 'user'
                                      ? Icons.person_outline
                                      : Icons.smart_toy_outlined,
                                  size: 20,
                                ),
                                title: Text('$owner「$hint」', maxLines: 2, overflow: TextOverflow.ellipsis),
                                subtitle: Text(left, style: const TextStyle(fontSize: 12)),
                                trailing: IconButton(
                                  icon: const Icon(Icons.delete_outline, size: 20),
                                  tooltip: l10n.deleteTimerTooltip,
                                  onPressed: () async {
                                    try {
                                      await ApiClient().deleteTimer(characterId, it['id'] as int);
                                      if (ctx.mounted) Navigator.pop(ctx);
                                      if (mounted) {
                                        ScaffoldMessenger.of(context).showSnackBar(
                                          SnackBar(content: Text(l10n.timerDeleted)),
                                        );
                                      }
                                    } catch (_) {
                                      if (ctx.mounted) {
                                        ScaffoldMessenger.of(ctx).showSnackBar(
                                          SnackBar(content: Text(l10n.deleteFailed)),
                                        );
                                      }
                                    }
                                  },
                                ),
                              );
                            },
                          ),
                        ),
                      ],
                    ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close)),
        ],
      ),
    );
  }

  /// 被引用消息是否已删除（会话消息全量加载，列表内找不到即视为已删）
  bool _isQuoteDeleted(ChatMessage msg, List<ChatMessage> all) {
    final quote = msg.quoteMeta;
    if (quote == null) return false;
    final id = (quote['message_id'] as num?)?.toInt();
    if (id == null) return true;
    return !all.any((m) => m.id == id);
  }

  /// 长按气泡菜单：气泡式小框（非抽屉），删除（仅最后一条）/引用/复制
  Future<void> _showBubbleMenu(Offset position, ChatMessage msg, int index) async {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    final isLast = index == chat.messages.length - 1;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox?;
    final size = overlay?.size ?? const Size(0, 0);
    final result = await showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(
        position.dx,
        position.dy,
        size.width - position.dx,
        size.height - position.dy,
      ),
      items: [
        if (isLast)
          PopupMenuItem<String>(
            value: 'delete',
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.delete_outline, size: 18),
              SizedBox(width: 8),
              Text(l10n.delete),
            ]),
          ),
        PopupMenuItem<String>(
          value: 'quote',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.format_quote, size: 18),
            SizedBox(width: 8),
            Text(l10n.quote),
          ]),
        ),
        PopupMenuItem<String>(
          value: 'copy',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.copy, size: 18),
            SizedBox(width: 8),
            Text(l10n.copy),
          ]),
        ),
      ],
    );
    if (result == null || !mounted) return;
    switch (result) {
      case 'delete':
        await _confirmDelete(msg);
        break;
      case 'quote':
        setState(() {
          _quote = {
            'message_id': msg.id,
            'sender': msg.isUser ? 'user' : 'ai',
            'content': StageText.excerpt(msg.content, max: 100),
          };
        });
        break;
      case 'copy':
        final stripped = StageText.parse(msg.content).text;
        await Clipboard.setData(ClipboardData(text: stripped.isEmpty ? msg.content : stripped));
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.copied), duration: const Duration(seconds: 1)),
          );
        }
        break;
    }
  }

  /// 删除确认（文案统一「删除」；删除为物理删除，连带小字/引用一并消失）
  Future<void> _confirmDelete(ChatMessage msg) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMessageTitle),
        content: Text(l10n.deleteMessageConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok == true && mounted) await _deleteMessage(msg.id);
  }

  /// AI 能力权限询问卡片（权限=每次询问 时显示，允许/拒绝后消失）
  Widget _buildPermissionCard() {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    final p = chat.pendingPermission!;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF8E6),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFFFFD57A)),
      ),
      child: Row(
        children: [
          const Icon(Icons.help_outline, size: 16, color: Color(0xFFB7791F)),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${l10n.aiWantsToCall}【${p.scopeLabel}】',
                  style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Color(0xFF7A5B12)),
                ),
                if (p.prompt.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      p.prompt,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11.5, color: Color(0xFF8A6D1F)),
                    ),
                  ),
              ],
            ),
          ),
          TextButton(
            onPressed: () => chat.denyPendingPermission(),
            child: Text(l10n.deny, style: const TextStyle(fontSize: 12.5, color: Color(0xFF8E8E93))),
          ),
          TextButton(
            onPressed: () => chat.approvePendingPermission(),
            child: Text(l10n.allow, style: const TextStyle(fontSize: 12.5, color: Color(0xFF007AFF), fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  /// 输入框上方引用条（可关闭）
  Widget _buildQuoteBar() {
    final l10n = AppLocalizations.of(context)!;
    final q = _quote ?? const <String, dynamic>{};
    final content = q['content'] as String? ?? '';
    final sender = q['sender'] == 'user' ? l10n.me : l10n.ta;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(Icons.format_quote, size: 14, color: Colors.grey.shade600),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '${l10n.quotePrefix} $sender：$content',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
            ),
          ),
          InkWell(
            onTap: () => setState(() => _quote = null),
            child: const Padding(
              padding: EdgeInsets.all(2),
              child: Icon(Icons.close, size: 16, color: Colors.grey),
            ),
          ),
        ],
      ),
    );
  }

  // "+" 键：微信式内嵌面板（点 + 展开在输入框上方，把输入框顶下去；再点收起）
  void _toggleMorePanel() {
    setState(() => _morePanelOpen = !_morePanelOpen);
    if (_morePanelOpen) {
      FocusManager.instance.primaryFocus?.unfocus();
    } else {
      _inputFocusNode.requestFocus();
    }
  }

  /// 更多功能面板：图片 / 文件（表情已剥离到「切换」小框）
  Widget _buildMorePanel() {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          _panelAction(
            icon: Icons.image_outlined,
            color: scheme.primary,
            label: l10n.image,
            sub: l10n.sendImage,
            onTap: () {
              setState(() => _morePanelOpen = false);
              _pickAndUploadImage();
            },
          ),
          const SizedBox(width: 16),
          _panelAction(
            icon: Icons.insert_drive_file_outlined,
            color: scheme.primary,
            label: l10n.file,
            sub: l10n.sendDoc,
            onTap: () {
              setState(() => _morePanelOpen = false);
              _pickAndSendFile();
            },
          ),
        ],
      ),
    );
  }

  Widget _panelAction({
    required IconData icon,
    required Color color,
    required String label,
    required String sub,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 30, color: color),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(fontSize: 12)),
            Text(sub, style: const TextStyle(fontSize: 10, color: Colors.grey)),
          ],
        ),
      ),
    );
  }

  /// 文件入口：选择本地文件 → 上传（后端提取摘要，AI 可读）
  Future<void> _pickAndSendFile() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    try {
      final result = await FilePicker.pickFiles();
      if (result == null || result.files.isEmpty) return;
      final path = result.files.single.path;
      if (path == null || !mounted) return;
      final file = File(path);
      if (file.lengthSync() > 20 * 1024 * 1024) {
        if (mounted) {
          final l10n = AppLocalizations.of(context)!;
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.fileTooLarge)));
        }
        return;
      }
      await chat.uploadFile(file);
    } catch (e) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatFileSendFail(e))));
      }
    }
  }

  /// 表情包入口：底部面板（包 tab + 下载 + 点击发送 emoji）
  void _showEmojiPanel() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => const _EmojiPanelSheet(),
    );
  }

  /// 语音发送：弹出按住说话录音面板
  Future<void> _showVoiceRecorder() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    if (!mounted) return;
    // 先收起键盘再弹录音面板：避免键盘 inset 与弹层开/合动画互相干扰
    // （曾在语音发送成功后残留大面积灰色块，刷新才消失）
    FocusManager.instance.primaryFocus?.unfocus();
    await Future<void>.delayed(const Duration(milliseconds: 150));
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _VoiceRecordSheet(chat: chat),
    );
  }

  /// 切换小框（气泡式，类似长按气泡菜单）：连续发送 / 语音发送 / 表情
  void _showSwitchMenu() {
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
          _showVoiceRecorder();
          break;
        case 'emoji':
          _showEmojiPanel();
          break;
      }
    });
  }

  Future<void> _pickAndUploadImage() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null || !mounted) return;
    // 选图后弹出配文框：气泡只显示图片+配文，OCR 内容只进 AI 上下文
    final caption = await _showImageCaptionDialog(File(picked.path));
    if (caption == null || !mounted) return;
    // 等待弹窗关闭动画完全结束再上传：若在动画期间立即 notifyListeners 重建页面，
    // 会触发 InheritedElement._dependents 断言红屏（_dependents.isEmpty is not true）
    await Future<void>.delayed(const Duration(milliseconds: 350));
    if (!mounted) return;
    await chat.uploadImage(File(picked.path), caption: caption);
  }

  /// 图片配文输入弹窗：图预览 + 文字输入。返回 null 表示取消。
  Future<String?> _showImageCaptionDialog(File imageFile) async {
    final l10n = AppLocalizations.of(context)!;
    final captionController = TextEditingController();
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatSendImage),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 260, maxHeight: 180),
                child: Image.file(imageFile, fit: BoxFit.contain),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: captionController,
              maxLines: 3,
              maxLength: 500,
              decoration: InputDecoration(
                hintText: l10n.chatImageCaption,
                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text(l10n.cancel),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, captionController.text.trim()),
            child: Text(l10n.send),
          ),
        ],
      ),
    );
    // 弹窗退出动画结束后再释放 controller（约 200ms 动画），
    // 避免 TextField 元素卸载前访问已销毁的 controller 触发框架断言
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      captionController.dispose();
    });
    return result;
  }


  @override
  void dispose() {
    appRouteObserver.unsubscribe(this);
    _controller.dispose();
    _inputFocusNode.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.watch<ChatProvider>();
    final char = chat.currentCharacter;

    // 自动滚动到底部
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 100),
          curve: Curves.easeOut,
        );
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            GestureDetector(
              onTap: () {
                if (chat.sessionId != null && char != null) {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
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
                  Text('${char?.name ?? ''} ${l10n.typing}', style: const TextStyle(fontSize: 12, color: Colors.grey)),
              ],
            ),
          ],
        ),
        actions: [
          if (char != null)
            IconButton(
              tooltip: l10n.eventClockTitle,
              icon: const Icon(Icons.timer_outlined, size: 22),
              onPressed: () => _showEventClockDialog(char.id),
            ),
        ],
      ),
      body: char == null
          ? Center(child: Text(l10n.chooseFriendFirst))
          : Column(
              children: [
                // 消息列表
                Expanded(
                  child: (chat.isLoading && chat.messages.isEmpty)
                      ? const Center(child: CircularProgressIndicator())
                      : ListView.builder(
                          controller: _scrollController,
                          padding: const EdgeInsets.all(12),
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
                                !_withinMinutes(prev.createdAt, msg.createdAt, 5);
                            return MessageBubble(
                              message: msg.content,
                              isUser: msg.isUser,
                              time: msg.createdAt,
                              showTime: showTime,
                              imageUrl: msg.imageUrl,
                              fileMeta: msg.fileMeta,
                              voiceMeta: msg.voiceMeta,
                              ttsMeta: msg.ttsMeta,
                              reasoning: msg.reasoning,
                              tools: msg.tools,
                              statusUpdate: msg.statusUpdate,
                              showReasoning: _reasoningLevel > 0,
                              showTools: _showTools,
                              serverUrl: ApiClient().baseUrl,
                              aiAvatarUrl: char.avatarUrl,
                              userAvatarUrl: context.read<SettingsProvider>().avatarUrl,
                              quoteMeta: msg.quoteMeta,
                              quoteDeleted: _isQuoteDeleted(msg, chat.messages),
                              onContinue: isLastAi ? () => chat.continueChat() : null,
                              onMenu: (offset) => _showBubbleMenu(offset, msg, index),
                            );
                          },
                        ),
                ),
                // 输入栏
                Container(
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surface,
                    boxShadow: [
                      BoxShadow(
                        color: Colors.grey.withValues(alpha: 0.2),
                        blurRadius: 4,
                        offset: const Offset(0, -2),
                      ),
                    ],
                  ),
                  padding: const EdgeInsets.fromLTRB(4, 8, 8, 8),
                  child: SafeArea(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (chat.pendingPermission != null) _buildPermissionCard(),
                        if (_quote != null) _buildQuoteBar(),
                        if (_morePanelOpen) _buildMorePanel(),
                        Row(
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
                            decoration: InputDecoration(
                              hintText: chat.batchMode ? l10n.inputHintBatch : l10n.inputHint,
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                              border: OutlineInputBorder(
                                borderRadius: BorderRadius.circular(24),
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
                        // 切换按钮：气泡式小框切换 连续发送 / 语音发送 / 表情
                        IconButton(
                          key: _switchKey,
                          tooltip: l10n.switchMode,
                          onPressed: _showSwitchMenu,
                          icon: Badge(
                            isLabelVisible: chat.batchMode && chat.pendingBatchCount > 0,
                            label: Text('${chat.pendingBatchCount}'),
                            child: Icon(
                              Icons.swap_horiz,
                              color: chat.batchMode ? Theme.of(context).colorScheme.tertiary : null,
                            ),
                          ),
                        ),
                        // 发送按钮：普通模式点按=发送单条；连续模式点按=发送全部收集的消息（模式切换已移至「切换」）
                        IconButton(
                          icon: Icon(
                            chat.batchMode ? Icons.playlist_add_check : Icons.send,
                            color: chat.batchMode ? Theme.of(context).colorScheme.tertiary : null,
                          ),
                          style: chat.batchMode
                              ? null
                              : IconButton.styleFrom(
                                  backgroundColor: Theme.of(context).colorScheme.primary,
                                  foregroundColor: Theme.of(context).colorScheme.onPrimary,
                                ),
                          onPressed: chat.batchMode ? () => chat.toggleBatchMode() : _sendMessage,
                        ),
                        ],
                      ),
                    ],
                  ),
                ),
                ),
              ],
            ),
    );
  }
}

/// 两条 ISO 时间（UTC）是否在 n 分钟内（北京时区等价，差值不变）
bool _withinMinutes(String a, String b, int minutes) {
  DateTime? pa = _parseIso(a);
  DateTime? pb = _parseIso(b);
  if (pa == null || pb == null) return false;
  return pa.difference(pb).abs().inMinutes < minutes;
}

DateTime? _parseIso(String s) {
  try {
    return DateTime.parse(s).toUtc();
  } catch (_) {
    return null;
  }
}

// ── 表情包面板：包 tab（下载/切换）+ emoji 网格（点击即发送）──
class _EmojiPanelSheet extends StatefulWidget {
  const _EmojiPanelSheet();

  @override
  State<_EmojiPanelSheet> createState() => _EmojiPanelSheetState();
}

class _EmojiPanelSheetState extends State<_EmojiPanelSheet> {
  List<Map<String, dynamic>>? _packs;
  List<Map<String, dynamic>> _custom = [];
  int _selected = -1; // -1 = 我的表情
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        ApiClient().getEmojiPacks(),
        ApiClient().getCustomEmojis(),
      ]);
      if (!mounted) return;
      setState(() {
        _packs = results[0].cast<Map<String, dynamic>>();
        _custom = (results[1] as List).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _download(int index) async {
    final l10n = AppLocalizations.of(context)!;
    final pack = _packs![index];
    final ok = await ApiClient().downloadEmojiPack(pack['id'] as String);
    if (ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatEmojiDownloaded(pack['name']))));
      await _load();
    }
  }

  Future<void> _remove(int index) async {
    final pack = _packs![index];
    final ok = await ApiClient().removeEmojiPack(pack['id'] as String);
    if (ok && mounted) {
      await _load();
    }
  }

  // 点击表情：未下载的包先自动下载再发送（后端全量返回明细，未下载也能预览）
  Future<void> _sendFromPack(int index, String emoji, String name) async {
    final pack = _packs![index];
    final downloaded = pack['downloaded'] as bool? ?? false;
    if (!downloaded) {
      final ok = await ApiClient().downloadEmojiPack(pack['id'] as String);
      if (!ok || !mounted) return;
      await _load();
    }
    if (mounted) _send(emoji, name);
  }

  void _send(String emoji, String name) {
    final chat = context.read<ChatProvider>();
    Navigator.pop(context);
    chat.sendMessage('$emoji $name');
  }

  void _sendCustom(Map<String, dynamic> emoji) {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    Navigator.pop(context);
    chat.sendEmoji(emoji['url'] as String? ?? '', emoji['name'] as String? ?? l10n.chatEmoji);
  }

  Future<void> _pickCustomEmoji() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final res = await FilePicker.pickFiles(
        type: FileType.image,
        allowMultiple: false,
      );
      if (res == null || res.files.isEmpty || res.files.single.path == null) return;
      final file = File(res.files.single.path!);
      await ApiClient().uploadCustomEmoji(file, l10n.myEmoji);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.emojiAdded)));
        await _load();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${l10n.addFailed}: $e')));
      }
    }
  }

  Future<void> _deleteCustom(Map<String, dynamic> emoji) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteEmojiTitle),
        content: Text(l10n.deleteEmojiConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    await ApiClient().deleteCustomEmoji(emoji['id'] as int);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final safe = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: safe),
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.45,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Row(
                children: [
                  Text(l10n.emojiPack, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            if (_loading)
              const Expanded(child: Center(child: CircularProgressIndicator()))
            else ...[
              SizedBox(
                height: 40,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        avatar: const Icon(Icons.person_outline, size: 16),
                        label: Text(l10n.mine),
                        selected: _selected == -1,
                        onSelected: (_) => setState(() => _selected = -1),
                      ),
                    ),
                    for (var i = 0; i < (_packs?.length ?? 0); i++)
                      Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          label: Text(_packs![i]['name'] as String? ?? ''),
                          selected: _selected == i,
                          onSelected: (_) => setState(() => _selected = i),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 6),
              Expanded(
                child: _selected == -1 ? _buildCustomBody() : _buildPackBody(_packs![_selected], _selected),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildCustomBody() {
    final l10n = AppLocalizations.of(context)!;
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
      ),
      itemCount: _custom.length + 1,
      itemBuilder: (ctx, i) {
        if (i == _custom.length) {
          return InkWell(
            onTap: _pickCustomEmoji,
            borderRadius: BorderRadius.circular(10),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.add_circle_outline, color: Theme.of(context).colorScheme.primary, size: 30),
                const SizedBox(height: 4),
                Text(l10n.chatEmojiAdd, style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
              ],
            ),
          );
        }
        final emoji = _custom[i];
        final url = (emoji['url'] as String? ?? '');
        final name = (emoji['name'] as String? ?? l10n.chatEmoji);
        return InkWell(
          onTap: () => _sendCustom(emoji),
          onLongPress: () => _deleteCustom(emoji),
          borderRadius: BorderRadius.circular(10),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  ApiClient().resolveUrl(url),
                  width: 46,
                  height: 46,
                  fit: BoxFit.cover,
                  errorBuilder: (c, e, s) => Container(
                    width: 46,
                    height: 46,
                    color: Colors.black.withValues(alpha: 0.05),
                    child: const Icon(Icons.broken_image, size: 20, color: Colors.grey),
                  ),
                ),
              ),
              const SizedBox(height: 3),
              Text(name, style: TextStyle(fontSize: 10, color: Colors.grey.shade500), maxLines: 1, overflow: TextOverflow.ellipsis),
            ],
          ),
        );
      },
    );
  }

  Widget _buildPackBody(Map<String, dynamic> pack, int index) {
    final l10n = AppLocalizations.of(context)!;
    final downloaded = pack['downloaded'] as bool? ?? false;
    final emojis = (pack['emojis'] as List? ?? []).cast<Map<String, dynamic>>();
    if (emojis.isEmpty) {
      // 兜底：无明细（旧缓存/异常）时保留手动下载入口
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(pack['description'] as String? ?? l10n.noEmoji, style: const TextStyle(fontSize: 13, color: Colors.grey)),
            const SizedBox(height: 14),
            FilledButton.icon(
              onPressed: () => _download(index),
              icon: const Icon(Icons.download),
              label: Text(l10n.downloadPack),
            ),
          ],
        ),
      );
    }
    return Column(
      children: [
        if (!downloaded)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
            child: Text(
              l10n.chatEmojiHint(pack['description'] ?? ''),
              style: const TextStyle(fontSize: 12, color: Colors.grey),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        Expanded(
          child: GridView.builder(
            padding: const EdgeInsets.all(12),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 5,
              mainAxisSpacing: 8,
              crossAxisSpacing: 8,
            ),
            itemCount: emojis.length + 1,
            itemBuilder: (ctx, i) {
              if (i == emojis.length) {
                // 删除包按钮（内置包不显示）
                final builtin = pack['builtin'] as bool? ?? false;
                if (builtin) return const SizedBox.shrink();
                return InkWell(
                  onTap: () => _remove(index),
                  borderRadius: BorderRadius.circular(10),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.delete_outline, color: Colors.grey.shade400, size: 22),
                      const SizedBox(height: 2),
                      Text(l10n.delete, style: TextStyle(fontSize: 10, color: Colors.grey.shade400)),
                    ],
                  ),
                );
              }
              final item = emojis[i];
              final emoji = item['emoji'] as String? ?? '';
              final name = item['name'] as String? ?? '';
              return InkWell(
                onTap: () => _sendFromPack(index, emoji, name),
                borderRadius: BorderRadius.circular(10),
                child: Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(emoji, style: const TextStyle(fontSize: 30)),
                      const SizedBox(height: 2),
                      Text(name, style: TextStyle(fontSize: 10, color: Colors.grey.shade500), maxLines: 1, overflow: TextOverflow.ellipsis),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

// ── 语音录制面板：按住说话，松手发送，上滑取消，60s 上限，失败重试 ──
class _VoiceRecordSheet extends StatefulWidget {
  final ChatProvider chat;
  const _VoiceRecordSheet({required this.chat});

  @override
  State<_VoiceRecordSheet> createState() => _VoiceRecordSheetState();
}

class _VoiceRecordSheetState extends State<_VoiceRecordSheet> {
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  bool _sending = false;
  bool _cancelArmed = false;
  int _elapsed = 0;
  String? _path;
  Offset? _pressStart;
  Timer? _timer;
  static const int _maxSeconds = 60;

  @override
  void dispose() {
    _timer?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _startRecord() async {
    final l10n = AppLocalizations.of(context)!;
    final hasPerm = await _recorder.hasPermission();
    if (!hasPerm) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatMicPermission)));
      }
      return;
    }
    // m4a(AAC) 压缩格式：60s 语音约几百 KB，避免 wav PCM 大文件上传在弱网下断连（曾导致"发送失败"误报）
    _path = '${Directory.systemTemp.path}/aicompanion_voice_${DateTime.now().millisecondsSinceEpoch}.m4a';
    try {
      await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: _path!);
      if (mounted) {
        setState(() {
          _recording = true;
          _cancelArmed = false;
          _elapsed = 0;
        });
      }
      // 60s 上限：倒计时自动停止并发送
      _timer?.cancel();
      _timer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (!mounted || !_recording) return;
        setState(() => _elapsed++);
        if (_elapsed >= _maxSeconds) {
          _timer?.cancel();
          _stopRecord(send: true);
        }
      });
    } catch (_) {}
  }

  Future<void> _stopRecord({required bool send}) async {
    if (!_recording) return;
    _timer?.cancel();
    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {}
    if (mounted) setState(() => _recording = false);
    if (!send || path == null || path.isEmpty) {
      _cleanup(path);
      return;
    }
    final file = File(path);
    if (!file.existsSync()) return;
    if (mounted) setState(() => _sending = true);
    final sec = _elapsed;
    await _sendWithRetry(file, sec);
  }

  Future<void> _sendWithRetry(File file, int sec) async {
    final l10n = AppLocalizations.of(context)!;
    while (mounted) {
      final ok = await widget.chat.uploadVoice(file, durationSec: sec);
      if (ok) break;
      if (!mounted) return;
      final retry = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.voiceSendFailed),
          content: Text(l10n.voiceRetryMsg),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(l10n.cancel),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(l10n.retry),
            ),
          ],
        ),
      );
      if (retry != true) {
        _cleanup(file.path);
        return;
      }
    }
    if (mounted) Navigator.pop(context);
  }

  void _cleanup(String? path) {
    try {
      if (path != null && File(path).existsSync()) File(path).deleteSync();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final hint = _sending
        ? l10n.sending
        : (_recording
            ? (_cancelArmed
                ? l10n.releaseToCancel
                : '${l10n.recordingPrefix} $_elapsed/$_maxSeconds${l10n.recordingSuffix}')
            : l10n.holdToTalk);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              hint,
              style: TextStyle(
                fontSize: 14,
                color: _cancelArmed ? Colors.orange.shade700 : (_recording ? Colors.red.shade400 : Colors.grey.shade600),
              ),
            ),
            const SizedBox(height: 16),
            GestureDetector(
              onLongPressStart: (d) {
                _pressStart = d.globalPosition;
                _startRecord();
              },
              onLongPressMoveUpdate: (d) {
                if (!_recording || _pressStart == null) return;
                final dy = d.globalPosition.dy - _pressStart!.dy;
                final armed = dy < -80;
                if (armed != _cancelArmed && mounted) {
                  setState(() => _cancelArmed = armed);
                }
              },
              onLongPressEnd: (_) {
                if (_cancelArmed) {
                  _stopRecord(send: false);
                  if (mounted) Navigator.pop(context);
                } else {
                  _stopRecord(send: true);
                }
              },
              onLongPressCancel: () => _stopRecord(send: false),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 120),
                width: 92,
                height: 92,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _cancelArmed ? Colors.orange.shade600 : (_recording ? Colors.red.shade400 : theme.colorScheme.primary),
                  boxShadow: _recording
                      ? [BoxShadow(color: (_cancelArmed ? Colors.orange : Colors.red).withValues(alpha: 0.35), blurRadius: 18, spreadRadius: 4)]
                      : null,
                ),
                child: Icon(
                  _cancelArmed ? Icons.keyboard_arrow_up : (_recording ? Icons.graphic_eq : Icons.mic),
                  color: Colors.white,
                  size: 38,
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextButton(
              onPressed: () {
                _stopRecord(send: false);
                Navigator.pop(context);
              },
              child: Text(l10n.cancel),
            ),
          ],
        ),
      ),
    );
  }
}
