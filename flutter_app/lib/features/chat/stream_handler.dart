
import 'dart:ui' as ui;


import '../../models/message.dart';
import 'message_appender.dart';
import '../../utils/service_l10n.dart';

/// 持有 SSE 流式状态（`_streamingBlockIds` / `_streamingMessage` / `isStreaming`）并向
/// ChatProvider 委托：delta 打字机、block 确认替换、done 收尾、reset_blocks 清块、
/// error/cold_war 提示、user_message 替换本地临时 id。
///
/// 通过构造函数注入 ChatProvider 的读写回调（getMessages / sessionId / serverNow /
/// onChanged / setError / setTyping / interruptVoicePlayback / enqueueTts），
/// 不反向依赖 ChatProvider，可独立单测。
class StreamHandler {
  StreamHandler({
    required void Function() onChanged,
    required List<ChatMessage> Function() getMessages,
    required int? Function() sessionId,
    required DateTime Function() serverNow,
    required void Function() interruptVoicePlayback,
    required void Function(String url) enqueueTts,
    required void Function(String?) setError,
    required void Function(bool) setTyping,
    void Function()? onReplySettled,
  })  : _onReplySettled = onReplySettled,
        _onChanged = onChanged,
        _getMessages = getMessages,
        _sessionId = sessionId,
        _serverNow = serverNow,
        _interruptVoicePlayback = interruptVoicePlayback,
        _enqueueTts = enqueueTts,
        _setError = setError,
        _setTyping = setTyping;

  final void Function() _onChanged;
  /// A54（2026-10-11）：一轮 AI 回复落地时回调 ⇒ 聊天页可见期间立刻标记已读，
  /// 不再等前台 30 秒自轮询（红点滞后的主因之一）。
  final void Function()? _onReplySettled;
  final List<ChatMessage> Function() _getMessages;
  final int? Function() _sessionId;
  final DateTime Function() _serverNow;
  final void Function() _interruptVoicePlayback;
  final void Function(String url) _enqueueTts;
  final void Function(String?) _setError;
  final void Function(bool) _setTyping;

  /// V2-1（2026-08-29）：只跟踪"当前轮"已确认正式块 ID；startStream / finishStreaming /
  /// reset_blocks 时清空，保证 reset_blocks 只清当前轮、不动多轮历史 AI 消息。
  final Set<int> _streamingBlockIds = {};

  /// 当前正在流式追加的 AI 气泡（isLocal，content 随 delta 增长）。
  /// §10（2026-10-11 用户拍板）：**不再有"流式气泡"这个载体**——生成期间不逐字上屏，
  /// 整块由 `block`（已落库）一次性上屏；所以原来的 `_streamingMessage`／`_appendDelta`／
  /// `splitStreamLeftover`（处理"delta 领先于块边界"的尾段搬运）一起摘掉。
  /// ⚠ 保留的是 SSE 管线本身，不是打字机：它还承载逐句 TTS、MCP 工具结果、冷场与错误事件，
  /// 整条切成"非流式一次性返回"会把这些一起砍掉（后端零改动，前端只不再渲染 delta 文本）。

  /// A1（#59）：本轮流式收集的 MCP 工具结果（tool_result 事件），随 block 确认附到正式块
  /// 的 extra_meta.tool_results 上，供气泡观察区可折叠展示。
  final List<Map<String, dynamic>> _streamToolResults = [];

  bool _isStreaming = false;
  bool get isStreaming => _isStreaming;

  List<ChatMessage> get _messages => _getMessages();

  /// 开始新一轮：清空本轮跟踪集合。调用方随后 setTyping + notify。
  ///
  /// §10：这里**不再插入空占位气泡**——以前靠它承接 delta 文本；现在生成期间只显示头像下
  /// 那三点闪烁光标（`isTyping` 驱动），整块内容等 `block` 到达才上屏。
  void startStream() {
    _streamingBlockIds.clear();
    _streamToolResults.clear();
    _isStreaming = true;
  }

  /// 结束本轮：清空 `_streamingBlockIds`，收尾流式气泡。
  /// V2-1：清空放在 `_isStreaming` 守卫之前——done 事件在流中途/收尾都会触发，
  /// `_isStreaming` 可能已是 false，但当前轮的跟踪集合都应在本轮结束后清空。
  void finishStreaming() {
    _streamingBlockIds.clear();
    _streamToolResults.clear();
    if (!_isStreaming) return;
    _isStreaming = false;
    // 移除空的流式占位气泡（历史遗留兜底：现在已不创建占位，留着是为了别的路径不残留空泡）
    MessageAppender.removeEmptyLocalBubbles(_messages);
    _setTyping(false);
    _onChanged();
    _onReplySettled?.call();
  }

  /// SSE 传输层异常回退时调用：关闭流式、清空流式气泡与占位（不触发 notify，由调用方收尾）。
  void abortStreaming() {
    _isStreaming = false;
    _streamToolResults.clear();
    // B3 修复（2026-09-01 审查）：传输层失败要整体回退到 WS 重新生成，必须把本轮
    // 已经确认上屏的正式块一并移除，否则半截正式回复 + WS 整份新回复 = 同一轮两条 AI 回答。
    // （reset_blocks 分支不在此处理：那是服务端主动换批量路径，块 id 清理已有独立逻辑。）
    if (_streamingBlockIds.isNotEmpty) {
      _messages.removeWhere((m) => _streamingBlockIds.contains(m.id));
      _streamingBlockIds.clear();
    }
    MessageAppender.removeEmptyLocalBubbles(_messages);
  }

  /// SSE 事件入口（等价原 ChatProvider.handleStreamEvent）。
  void handleEvent(Map<String, dynamic> event) {
    final type = event['type'] as String?;
    switch (type) {
      case 'user_message':
        // 用户消息正式落库回传：替换本地临时 id。
        // B1 修复（2026-09-01 审查）：WS 负载是 {"data":{...}} 嵌套，SSE 端点把负载展平为
        // 同级字段，两种形状都要兼容——否则 SSE 主链路下用户气泡永远挂负的本地临时 id
        // （按 id 的撤回/删除/引用在当前会话内全部失效，重进页面才恢复）。
        Map<String, dynamic>? info;
        final nested = event['data'];
        if (nested is Map<String, dynamic>) {
          info = nested;
        } else {
          info = Map<String, dynamic>.fromEntries(
            event.entries.where((e) => e.key != 'type'),
          );
        }
        if (info.isNotEmpty) {
          MessageAppender.replaceTempUserMessage(_messages, info);
        }
        _onChanged();
      case 'typing':
        // #63 机制2：回复延迟信号——服务端在生成前推送 typing（可带 delay 秒），
        // 前端保持"输入中..."直到首块内容（delta/block/done）到达再清除，不按固定时长。
        _setTyping(event['is_typing'] as bool? ?? false);
        // §10 修订（10-11 用户指出）：必须 notify。以前顶栏三点顺带掩盖了这个缺口——
        // 现在生成中的可见反馈**只**由 isTyping 驱动（列表末尾的光标行），不 notify 就是一片静止。
        // WS 那条腿本来就是 `_setTyping` 后紧跟 `_onChanged`（ws_handler.dart 的 typing 分支），
        // 两条腿这里本来就不该不一致。
        _onChanged();
      case 'delta':
        // §10：delta 文本**不再上屏**（人发消息本来就是一整块，弱网下逐字吐字一顿一顿）。
        // 这里连 _setTyping(false) 都不做——生成中的可见反馈交给**列表末尾那条光标行**（chat_screen 依 `isTyping` 渲染），
        // 光标要留到**第一个 block 落地**才熄灭（下面 block 分支），否则中间会出现
        // "既没有字、也没有光标"的一段空窗。事件本身仍被消费（不 return，继续走 switch）。
        break;
      case 'reset_blocks':
        // P2-NEW（2026-08-29）：TTS consumer 中途死亡回退批量路径会先删旧块再全量新建，新块 ID 与
        // 旧块不同，前端按块 id 去重（_confirmStreamBlock）永远不命中 → 先清除本轮已确认的 AI 正式块
        // （非用户/非本地临时/非 system），避免重复气泡。此事件先于新 block 到达。
        // V2-1（2026-08-29）：只清除本轮 _streamingBlockIds 内的正式块，不动会话历史 AI 消息，
        // 避免多轮对话中任意一轮 TTS 回退抹掉之前所有轮次的 AI 回复。
        // V2-4（2026-08-29）：同时打断并清空 TTS 队列，避免旧块音频与新块音频交错播放。
        _messages.removeWhere((m) => _streamingBlockIds.contains(m.id));
        _streamingBlockIds.clear();
        _interruptVoicePlayback();
        _onChanged();
      case 'block':
        // 完整语义块（已落库）：确认替换当前流式气泡，开启下一气泡；语音逐句 TTS 入队顺序播放
        final msg = ChatMessage.fromJson(event);
        _confirmBlock(msg);
        _enqueueTtsPlayback(msg);
        // §10：整块上屏的那一刻才收掉"生成中"光标（对应 delta 分支不再清它）
        _setTyping(false);
      case 'tool_result':
        // A1（#59）流式路径 MCP 工具循环：独立流尾事件，工具结果文本随块确认附到正式块，
        // 前端在气泡观察区可折叠展示（成功/失败各一块）。
        _streamToolResults.add(event);
        _attachToolResults();
      case 'done':
        finishStreaming();
      case 'error':
        _setError(event['detail'] as String?);
        // P2-2：错误也要清除"输入中..."（与 ws_handler.dart 的 error case 一致）
        _setTyping(false);
        // 服务端已内部回退非流式 chunked（仍会继续推 block/done），这里只提示，不重复发送
        _onChanged();
      case 'cold_war':
        final cwL10n = ServiceL10n(ui.PlatformDispatcher.instance.locale.languageCode.toLowerCase().startsWith('en') ? 'en' : 'zh');
        _messages.add(ChatMessage(
          id: DateTime.now().millisecondsSinceEpoch,
          sessionId: _sessionId() ?? 0,
          senderType: 'system',
          isLocal: true,
          content: event['message'] as String? ?? cwL10n.noResponseFallback,
          createdAt: _serverNow().toIso8601String(),
        ));
        finishStreaming();
    }
  }

  /// A1（#59）＋§10：MCP 工具结果**先攒在本轮集合里**，等 `block` 确认时附到正式块的
  /// extra_meta.tool_results（见 `_confirmBlock` 开头）。§10 之前是"附到当前流式气泡"，
  /// 现在没有流式气泡可附，工具结果因此**不会在块确认前凭空造一个气泡**。
  void _attachToolResults() {
    _onChanged();
  }

  void _confirmBlock(ChatMessage msg) {
    // A1（#59）：把本轮流式收集的 MCP 工具结果附到正式块（extra_meta.tool_results），
    // 随气泡观察区可折叠展示。只附一次（首个确认的块），避免多块重复。
    if (_streamToolResults.isNotEmpty) {
      msg.extraMeta['tool_results'] = List<Map<String, dynamic>>.from(_streamToolResults);
      _streamToolResults.clear();
    }
    // P3-5（2026-08-29）：TTS 流式 consumer 中途死亡时，服务端走批量回退路径会全量重推 block
    // （P2-B 数据完整性兜底），此前已确认的块会被重复推送。按块 id 去重：messages 已含同一 id
    // 的正式块（非本地临时气泡）时不重复添加，避免出现重复气泡。
    if (msg.id > 0) {
      // V2-1：记录本轮已确认的正式块 ID（reset_blocks 据此只清当前轮）
      _streamingBlockIds.add(msg.id);
      if (MessageAppender.upsertConfirmedBlock(_messages, msg)) {
        _onChanged();
        return;
      }
    }
    _messages.add(msg);
    _onChanged();
  }

  /// 把带 tts_url 的句块音频入队，供逐句顺序播放。
  void _enqueueTtsPlayback(ChatMessage msg) {
    final tts = msg.ttsMeta;
    final url = tts?['url'];
    if (url is String && url.isNotEmpty) {
      _enqueueTts(url);
    }
  }
}
