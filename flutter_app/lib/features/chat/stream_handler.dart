
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';

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
  })  : _onChanged = onChanged,
        _getMessages = getMessages,
        _sessionId = sessionId,
        _serverNow = serverNow,
        _interruptVoicePlayback = interruptVoicePlayback,
        _enqueueTts = enqueueTts,
        _setError = setError,
        _setTyping = setTyping;

  final void Function() _onChanged;
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
  ChatMessage? _streamingMessage;

  /// A1（#59）：本轮流式收集的 MCP 工具结果（tool_result 事件），随 block 确认附到正式块
  /// 的 extra_meta.tool_results 上，供气泡观察区可折叠展示。
  final List<Map<String, dynamic>> _streamToolResults = [];

  bool _isStreaming = false;
  bool get isStreaming => _isStreaming;

  List<ChatMessage> get _messages => _getMessages();

  ChatMessage _buildLocalAiBubble(String content) {
    return ChatMessage(
      id: -DateTime.now().millisecondsSinceEpoch,
      sessionId: _sessionId() ?? 0,
      senderType: 'ai',
      isLocal: true,
      content: content,
      createdAt: _serverNow().toIso8601String(),
    );
  }

  /// 开始新一轮：清空 `_streamingBlockIds`，建立流式占位气泡。调用方随后 setTyping + notify。
  void startStream() {
    _streamingBlockIds.clear();
    _streamToolResults.clear();
    _isStreaming = true;
    _streamingMessage = _buildLocalAiBubble('');
    _messages.add(_streamingMessage!);
  }

  /// 结束本轮：清空 `_streamingBlockIds`，收尾流式气泡。
  /// V2-1：清空放在 `_isStreaming` 守卫之前——done 事件在流中途/收尾都会触发，
  /// `_isStreaming` 可能已是 false，但当前轮的跟踪集合都应在本轮结束后清空。
  void finishStreaming() {
    _streamingBlockIds.clear();
    _streamToolResults.clear();
    if (!_isStreaming) return;
    _isStreaming = false;
    // 移除空的流式占位气泡（若无 delta 即被打断，如 cold_war/异常）；有内容则保留为本地消息。
    MessageAppender.removeEmptyLocalBubbles(_messages);
    _streamingMessage = null;
    _setTyping(false);
    _onChanged();
  }

  /// SSE 传输层异常回退时调用：关闭流式、清空流式气泡与占位（不触发 notify，由调用方收尾）。
  void abortStreaming() {
    _isStreaming = false;
    _streamingMessage = null;
    _streamToolResults.clear();
    MessageAppender.removeEmptyLocalBubbles(_messages);
  }

  /// SSE 事件入口（等价原 ChatProvider.handleStreamEvent）。
  void handleEvent(Map<String, dynamic> event) {
    final type = event['type'] as String?;
    switch (type) {
      case 'user_message':
        // 用户消息正式落库回传：替换本地临时 id
        MessageAppender.replaceTempUserMessage(_messages, event['data'] as Map<String, dynamic>?);
        _onChanged();
      case 'typing':
        // #63 机制2：回复延迟信号——服务端在生成前推送 typing（可带 delay 秒），
        // 前端保持"输入中..."直到首块内容（delta/block/done）到达再清除，不按固定时长。
        _setTyping(event['is_typing'] as bool? ?? false);
      case 'delta':
        final text = event['text'] as String? ?? '';
        if (text.isNotEmpty) {
          _appendDelta(text);
          // #63 机制2：首块内容到达即清除"输入中..."（保持"输入中..."直到首块内容）
          _setTyping(false);
        }
      case 'reset_blocks':
        // P2-NEW（2026-08-29）：TTS consumer 中途死亡回退批量路径会先删旧块再全量新建，新块 ID 与
        // 旧块不同，前端按块 id 去重（_confirmStreamBlock）永远不命中 → 先清除本轮已确认的 AI 正式块
        // （非用户/非本地临时/非 system），避免重复气泡。此事件先于新 block 到达。
        // V2-1（2026-08-29）：只清除本轮 _streamingBlockIds 内的正式块，不动会话历史 AI 消息，
        // 避免多轮对话中任意一轮 TTS 回退抹掉之前所有轮次的 AI 回复。
        // V2-4（2026-08-29）：同时打断并清空 TTS 队列，避免旧块音频与新块音频交错播放。
        _messages.removeWhere((m) => _streamingBlockIds.contains(m.id));
        _streamingBlockIds.clear();
        _streamingMessage = null;
        _interruptVoicePlayback();
        _onChanged();
      case 'block':
        // 完整语义块（已落库）：确认替换当前流式气泡，开启下一气泡；语音逐句 TTS 入队顺序播放
        final msg = ChatMessage.fromJson(event);
        _confirmBlock(msg);
        _enqueueTtsPlayback(msg);
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

  void _appendDelta(String text) {
    var sm = _streamingMessage;
    if (sm == null) {
      sm = _buildLocalAiBubble(text);
      _streamingMessage = sm;
      _messages.add(sm);
      _onChanged();
      return;
    }
    final updated = sm.copyWith(content: sm.content + text);
    final idx = _messages.lastIndexWhere((m) => identical(m, sm));
    if (idx >= 0) {
      _messages[idx] = updated;
    } else {
      _messages.add(updated);
    }
    _streamingMessage = updated;
    _onChanged();
  }

  /// A1（#59）：把流式收集的 MCP 工具结果写到当前流式气泡的 extra_meta.tool_results 上。
  void _attachToolResults() {
    final sm = _streamingMessage;
    if (sm != null) {
      // 流式气泡默认 extra_meta 为 const {}（不可变），通过 copyWith 换成含 tool_results 的可变 Map。
      final updated = sm.copyWith(extraMeta: {
        ...sm.extraMeta,
        'tool_results': List<Map<String, dynamic>>.from(_streamToolResults),
      });
      final idx = _messages.lastIndexWhere((m) => identical(m, sm));
      if (idx >= 0) {
        _messages[idx] = updated;
      } else {
        _messages.add(updated);
      }
      _streamingMessage = updated;
    }
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
    final sm = _streamingMessage;
    if (sm != null) {
      final idx = _messages.lastIndexWhere((m) => identical(m, sm));
      if (idx >= 0) {
        _messages[idx] = msg;
      } else {
        _messages.add(msg);
      }
      // 语音逐句 TTS 边流式边推 block（delta 打字机可能领先于块确认）。
      // 若流式气泡已有超出本句的内容（块边界被 delta 抢先），保留其尾段为新流式气泡，
      // 避免块确认时闪退已打字的后续文本；否则清空流式气泡等待下一句。
      final leftover = splitStreamLeftover(sm.content, msg.content);
      if (leftover.isEmpty) {
        _streamingMessage = null;
      } else {
        final next = sm.copyWith(content: leftover);
        _streamingMessage = next;
        _messages.add(next);
      }
    } else {
      _messages.add(msg);
      _streamingMessage = null;
    }
    _onChanged();
  }

  /// 若流式气泡正文以块正文为前缀，返回其剩余尾段；否则返回 ''（不拆分，回退旧行为）。
  @visibleForTesting
  String splitStreamLeftover(String accumulated, String blockContent) {
    if (accumulated.isEmpty || blockContent.isEmpty) return '';
    if (!accumulated.startsWith(blockContent)) return '';
    return accumulated.substring(blockContent.length);
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
