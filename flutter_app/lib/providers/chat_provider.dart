
import 'package:flutter/foundation.dart';
import '../models/message.dart';
import '../models/character.dart';
import '../services/api_client.dart';
import '../services/notification_service.dart';
import '../services/api/permission_api.dart';
import '../services/websocket_service.dart';
import '../services/voice_playback_queue.dart';
import '../features/chat/stream_handler.dart';
import '../features/chat/ws_handler.dart';
import '../features/chat/message_appender.dart';
import '../utils/service_l10n.dart';
import '../utils/app_lang.dart';

export '../features/chat/ws_handler.dart' show PendingPermissionRequest;

class ChatProvider extends ChangeNotifier {
  ApiClient get _api => ApiClient();
  final WebSocketService _ws = WebSocketService();
  int _userId = 0;
  String _localeCode = 'zh'; // 界面语言（i18n），随消息带给后端

  List<ChatMessage> _messages = [];
  AICharacter? _currentCharacter;
  int? _sessionId;
  bool _isLoading = false;
  bool _isTyping = false;
  bool _isSending = false;
  bool _batchMode = false;
  int _pendingBatchCount = 0;
  String? _error;
  String? _pendingGreeting; // 会话开始后自动发送的首条问候（Onboarding 首次消息用）
  PendingPermissionRequest? _pendingPermission;
  PendingPermissionRequest? get pendingPermission => _pendingPermission;

  final VoicePlaybackQueue _voicePlayback;
  late final StreamHandler _streamHandler;
  late final WsHandler _wsHandler;

  ChatProvider({VoicePlaybackQueue? voicePlayback})
      : _voicePlayback = voicePlayback ?? VoicePlaybackQueue() {
    _streamHandler = StreamHandler(
      onChanged: () => notifyListeners(),
      getMessages: () => _messages,
      sessionId: () => _sessionId,
      serverNow: () => _api.serverNow(),
      interruptVoicePlayback: () => _voicePlayback.interrupt(),
      enqueueTts: (url) => _voicePlayback.enqueue(url),
      setError: (e) => _error = e,
      setTyping: (b) => _isTyping = b,
    );
    _wsHandler = WsHandler(
      onChanged: () => notifyListeners(),
      getMessages: () => _messages,
      sessionId: () => _sessionId,
      serverNow: () => _api.serverNow(),
      userId: () => _userId,
      characterId: () => _currentCharacter?.id,
      localeCode: () => _localeCode,
      setTyping: (b) => _isTyping = b,
      setError: (e) => _error = e,
      setPendingPermission: (p) => _pendingPermission = p,
      rawSend: (p) => _ws.send(p),
    );
  }

  List<ChatMessage> get messages => MessageAppender.sorted(_messages);
  AICharacter? get currentCharacter => _currentCharacter;
  int? get sessionId => _sessionId;
  bool get isLoading => _isLoading;
  bool get isTyping => _isTyping;
  bool get isSending => _isSending;
  bool get batchMode => _batchMode;
  bool get isStreaming => _streamHandler.isStreaming;
  int get pendingBatchCount => _pendingBatchCount;
  String? get error => _error;

  void setUserId(int userId) {
    _userId = userId;
  }

  void setLocaleCode(String code) {
    _localeCode = code;
  }

  void setCharacter(AICharacter character) {
    _currentCharacter = character;
    _sessionId = null;
    _messages = [];
    _pendingBatchCount = 0;
    _batchMode = false;
    notifyListeners();
  }

  /// 设置会话开始后自动发送的首条消息（Onboarding 首次进入聊天页的欢迎语）。
  /// 会话创建成功后自动发送一次；若届时尚未连接则忽略（仅作引导辅助）。
  void setInitialGreeting(String text) {
    _pendingGreeting = text;
  }

  void toggleBatchMode() {
    _batchMode = !_batchMode;
    if (!_batchMode && _pendingBatchCount > 0) {
      _sendBatch();
    }
    notifyListeners();
  }

  void _sendBatch() {
    // Find all pending user messages (those without proper server IDs)
    // In batch mode, messages are sent via batch_messages WS type
    if (_sessionId == null || _currentCharacter == null || _pendingBatchCount == 0) return;

    // Collect the pending messages (last N user messages without responses)
    // Use the stored count to know how many to batch
    final pendingMsgs = _messages.sublist(_messages.length - _pendingBatchCount);
    final texts = pendingMsgs.map((m) => m.content).toList();

    // Send via WS
    _wsHandler.sendBatch(texts);

    _pendingBatchCount = 0;
    _isTyping = true;
    notifyListeners();
  }

  Future<void> startSession() async {
    if (_currentCharacter == null) return;
    _isLoading = true;
    notifyListeners();

    try {
      await _api.ensureServerOffset();
      final session = await _api.createSession(_currentCharacter!.id);
      _sessionId = session['id'] as int;

      // 连接 WebSocket
      _ws.connect(_api.baseUrl, _sessionId!, token: _api.token, onMessage: _wsHandler.handleWsMessage);

      // 加载已有消息
      _messages = await _api.getMessages(_sessionId!);

      // 加载已有消息
      try {
        await NotificationService().markRead(_sessionId!);
      } catch (_) {}

      _isLoading = false;
      notifyListeners();

      // 会话就绪后自动发送首条问候（Onboarding 首次消息；发送后清空避免重复）
      if (_pendingGreeting != null) {
        final greeting = _pendingGreeting!;
        _pendingGreeting = null;
        // WebSocket 已连接，走既有发送链路，成功后用户消息在气泡实时可见
        sendMessage(greeting);
      }
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.connectFailedPrefix();
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> approvePendingPermission() async {
    final p = _pendingPermission;
    if (p == null) return;
    try {
      await _api.approvePermissionAction(p.actionId);
    } catch (_) {}
    _pendingPermission = null;
    notifyListeners();
  }

  Future<void> denyPendingPermission() async {
    final p = _pendingPermission;
    if (p == null) return;
    try {
      await _api.denyPermissionAction(p.actionId);
    } catch (_) {}
    _pendingPermission = null;
    notifyListeners();
  }

  void continueChat() {
    if (_sessionId == null || _currentCharacter == null) return;
    _wsHandler.sendContinue(
      characterId: _currentCharacter!.id,
      lastMessageId: _messages.isNotEmpty ? _messages.last.id : 0,
    );
  }

  Future<void> sendMessage(String content, {Map<String, dynamic>? quote}) async {
    if (content.trim().isEmpty || _sessionId == null) return;
    // B8 修复（2026-09-01 审查）：连续发送（批量收集）模式允许累积；普通模式上一轮
    // 未结束时拒绝重入——避免两条 SSE 并发互相清空块跟踪、双份回复与双份计费。
    if (!_batchMode && _streamHandler.isStreaming) return;
    await _api.ensureServerOffset();

    // 添加用户消息（引用：extraMeta 存 quote，气泡顶部渲染引用块）
    _messages.add(ChatMessage(
      id: DateTime.now().millisecondsSinceEpoch,
      sessionId: _sessionId!,
      senderType: 'user',
      isLocal: true,
      content: content,
      createdAt: _api.serverNow().toIso8601String(),
      extraMeta: quote != null ? <String, dynamic>{'quote': quote} : const <String, dynamic>{},
    ));
    notifyListeners();

    if (_batchMode) {
      // 连续发送模式：只收集消息到列表，不发送
      _pendingBatchCount++;
      return;
    }
    // 主私聊链路：SSE 真流式发送（增量打字机 + 语义块确认）
    await _sendViaStream(content, quote);
  }

  /// SSE 流式发送入口：建立流式气泡，事件驱动增量渲染与块确认。
  Future<void> _sendViaStream(
    String content,
    Map<String, dynamic>? quote, {
    bool tts = false,
    bool saveUserMessage = true,
  }) async {
    if (_sessionId == null || _currentCharacter == null) return;
    // V2-1：新一轮开始 → 清空上一轮跟踪的正式块 ID，reset_blocks 只清当前轮
    _streamHandler.startStream();
    _isTyping = true;
    notifyListeners();
    try {
      await _api.streamMessage(
        _sessionId!,
        content,
        lang: _localeCode,
        quote: quote,
        tts: tts,
        saveUserMessage: saveUserMessage,
        onEvent: _streamHandler.handleEvent,
      );
      // 流正常收尾；若 done 事件未触发兜底结束
      if (_streamHandler.isStreaming) _streamHandler.finishStreaming();
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.streamSendFailedErr(e);
      _fallbackToWebSocket(content, quote);
    }
  }

  /// 处理 SSE 事件（delta 打字机 / block·done 确认替换 / error 提示 / cold_war / user_message 回传）。
  /// 委托给 [_streamHandler]（拆分后逻辑不变）。
  @visibleForTesting
  void handleStreamEvent(Map<String, dynamic> event) {
    _streamHandler.handleEvent(event);
  }

  /// SSE 传输层异常：回退非流式 WS 请求（用户消息已本地展示，不再重复添加）。
  void _fallbackToWebSocket(String content, Map<String, dynamic>? quote) {
    _streamHandler.abortStreaming();
    _isTyping = true;
    if (_currentCharacter != null) {
      _wsHandler.sendMessage({
        'type': 'chat_message',
        'character_id': _currentCharacter!.id,
        'content': content,
        'user_id': _userId,
        'lang': _localeCode,
        if (quote != null) 'quote': quote,
      });
    }
    notifyListeners();
  }

  Future<void> uploadImage(dynamic file, {String caption = ""}) async {
    if (_sessionId == null || _currentCharacter == null) return;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    try {
      final result = await _api.uploadChatImage(_sessionId!, file, userId: _userId, caption: caption, lang: _localeCode);
      MessageAppender.appendMessageResult(_messages, result, 'image_message');
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.imageSendFailedErr(e);
    }
    _isSending = false;
    _isTyping = false;
    notifyListeners();
  }

  /// 上传文件消息：HTTP 上传 → 后端摘要 → 加入文件消息与 AI 回复。
  Future<void> uploadFile(dynamic file, {String lang = 'zh'}) async {
    if (_sessionId == null || _currentCharacter == null) return;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    try {
      final result = await _api.uploadChatFile(_sessionId!, file, lang: lang);
      MessageAppender.appendMessageResult(_messages, result, 'file_message');
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.fileSendFailedErr(e);
    }
    _isSending = false;
    _isTyping = false;
    notifyListeners();
  }

  /// 上传语音消息：HTTP 上传转写落库语音消息 → SSE（tts=True）逐句生成 AI 语音回复。
  /// 新语音发出前打断上一轮语音播报；AI 回复按句实时推送并顺序播放。
  Future<bool> uploadVoice(dynamic file, {int durationSec = 0, String lang = 'zh'}) async {
    if (_sessionId == null || _currentCharacter == null) return false;
    _isSending = true;
    _isTyping = true;
    _voicePlayback.interrupt();
    notifyListeners();
    var ok = false;
    try {
      final result = await _api.uploadChatVoice(_sessionId!, file, durationSec: durationSec, lang: lang);
      final vm = result['voice_message'];
      if (vm is Map<String, dynamic>) {
        _messages.add(ChatMessage.fromJson(vm));
      }
      // 转写文本经 SSE 触发 AI 逐句语音回复；用户消息已落库（voice_message），SSE 不重复落用户消息。
      final content = (result['content'] as String?) ?? (result['transcript'] as String?) ?? '';
      if (content.trim().isNotEmpty) {
        await _sendViaStream(content, null, tts: true, saveUserMessage: false);
        ok = true;
      }
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.voiceSendFailedErr(e);
    }
    _isSending = false;
    _isTyping = false;
    notifyListeners();
    return ok;
  }

  /// 发送表情消息（自定义/市场贴图）：引用表情图，AI 经表情名 + 含义（meaning）理解
  Future<void> sendEmoji(String emojiUrl, String name, {String meaning = ''}) async {
    if (_sessionId == null || _currentCharacter == null) return;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    try {
      final result = await _api.sendEmojiMessage(_sessionId!, emojiUrl, name, lang: _localeCode, meaning: meaning);
      MessageAppender.appendMessageResult(_messages, result, 'emoji_message');
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.emojiSendFailedErr(e);
    }
    _isSending = false;
    _isTyping = false;
    notifyListeners();
  }

  Future<void> deleteMessage(int messageId) async {
    try {
      await _api.deleteMessage(messageId);
      _messages.removeWhere((m) => m.id == messageId);
      notifyListeners();
    } catch (e) {
      final l10n = ServiceL10n(await appLang());
      _error = l10n.deleteFailedErr(e);
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _voicePlayback.dispose();
    _ws.disconnect();
    super.dispose();
  }
}
