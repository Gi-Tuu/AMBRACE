
import 'package:flutter/foundation.dart';
import '../models/message.dart';
import '../models/character.dart';
import '../services/api_client.dart';
import '../services/notification_service.dart';
import '../services/api/permission_api.dart';
import '../services/websocket_service.dart';
import '../services/voice_playback_queue.dart';

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
  /// SSE 真流式进行中（chat_screen 据此显示打字机光标）
  bool _streaming = false;
  /// 当前正在流式追加的 AI 气泡（isLocal，content 随 delta 增长）
  ChatMessage? _streamingMessage;
  /// 本轮 SSE 流式已确认的正式块 ID（V2-1）：reset_blocks 只清除这些正式块，
  /// 不动会话里的历史 AI 消息（多轮对话 TTS 回退不再清空整段历史）。在 `_sendViaStream`
  /// 开始（新一轮）与 `_finishStreaming`（本轮结束）时清空，保证只跟踪"当前轮"。
  final Set<int> _streamingBlockIds = {};

  int _pendingBatchCount = 0;
  String? _error;
  String? _pendingGreeting; // 会话开始后自动发送的首条问候（Onboarding 首次消息用）
  PendingPermissionRequest? _pendingPermission;
  PendingPermissionRequest? get pendingPermission => _pendingPermission;

  final VoicePlaybackQueue _voicePlayback;

  ChatProvider({VoicePlaybackQueue? voicePlayback})
      : _voicePlayback = voicePlayback ?? VoicePlaybackQueue();

  List<ChatMessage> get messages => List<ChatMessage>.from(_messages)..sort((a, b) {
      final ta = DateTime.tryParse(a.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
      final tb = DateTime.tryParse(b.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
      int c = ta.compareTo(tb);
      if (c != 0) return c;
      if (a.isLocal != b.isLocal) return a.isLocal ? -1 : 1;
      return a.id.compareTo(b.id);
    });
  AICharacter? get currentCharacter => _currentCharacter;
  int? get sessionId => _sessionId;
  bool get isLoading => _isLoading;
  bool get isTyping => _isTyping;
  bool get isSending => _isSending;
  bool get batchMode => _batchMode;
  bool get isStreaming => _streaming;
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
    _ws.send({
      "type": "batch_messages",
      "character_id": _currentCharacter!.id,
      "messages": texts,
      "user_id": _userId,
      "lang": _localeCode,
    });
    
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
      _ws.connect(_api.baseUrl, _sessionId!, token: _api.token, onMessage: _onWsMessage);

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
      _error = '连接失败: ';
      _isLoading = false;
      notifyListeners();
    }
  }

  void _onWsMessage(Map<String, dynamic> data) {
    final type = data['type'] as String?;
    switch (type) {
      case 'user_message':
        // 用户消息正式落库回传：替换本地临时 id（保证删除可用；2026-08-15）
        final userMsg = ChatMessage.fromJson(data['data'] as Map<String, dynamic>);
        final localIdx = _messages.lastIndexWhere(
            (m) => m.isLocal && m.senderType == 'user');
        if (localIdx >= 0) {
          _messages[localIdx] = userMsg;
        } else {
          _messages.add(userMsg);
        }
        notifyListeners();
      case 'ai_response':
        final msgData = data['data'] as Map<String, dynamic>;
        // 只接收当前会话的消息：其他会话的主动消息（如另一个角色的私信）不得显示/插入本会话
        if (msgData['session_id'] != null && msgData['session_id'] != _sessionId) {
          return;
        }
        _messages.add(ChatMessage.fromJson(msgData));
        _isTyping = false;
        notifyListeners();
      case 'cold_war':
        // 冷战拦截（v3）：角色生气冷战期不回复，本地显示系统提示
        _messages.add(ChatMessage(
          id: DateTime.now().millisecondsSinceEpoch,
          sessionId: _sessionId ?? 0,
          senderType: 'system',
          isLocal: true,
          content: data['message'] as String? ?? 'TA 暂时没有回应你',
          createdAt: _api.serverNow().toIso8601String(),
        ));
        _isTyping = false;
        notifyListeners();
      case 'typing':
        _isTyping = data['is_typing'] as bool? ?? false;
        notifyListeners();
      case 'error':
        _error = data['detail'] as String?;
        _isTyping = false;
        notifyListeners();
      case 'permission_request':
        final pd = data['data'] as Map<String, dynamic>?;
        if (pd != null) {
          _pendingPermission = PendingPermissionRequest(
            actionId: pd['action_id'] as int? ?? 0,
            scope: pd['scope'] as String? ?? '',
            scopeLabel: pd['scope_label'] as String? ?? '能力',
            prompt: pd['prompt'] as String? ?? '',
            characterId: pd['character_id'] as int? ?? 0,
          );
          notifyListeners();
        }
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
    _ws.send({
      'type': 'continue_chat',
      'character_id': _currentCharacter!.id,
      'last_message_id': _messages.isNotEmpty ? _messages.last.id : 0,
      'user_id': _userId,
      'lang': _localeCode,
    });
  }

  Future<void> sendMessage(String content, {Map<String, dynamic>? quote}) async {
    if (content.trim().isEmpty || _sessionId == null) return;
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
    _streamingBlockIds.clear();
    _isTyping = true;
    _streaming = true;
    _streamingMessage = ChatMessage(
      id: -DateTime.now().millisecondsSinceEpoch,
      sessionId: _sessionId!,
      senderType: 'ai',
      isLocal: true,
      content: '',
      createdAt: _api.serverNow().toIso8601String(),
    );
    _messages.add(_streamingMessage!);
    notifyListeners();
    try {
      await _api.streamMessage(
        _sessionId!,
        content,
        lang: _localeCode,
        quote: quote,
        tts: tts,
        saveUserMessage: saveUserMessage,
        onEvent: handleStreamEvent,
      );
      // 流正常收尾；若 done 事件未触发兜底结束
      if (_streaming) _finishStreaming();
    } catch (e) {
      _error = '流式发送失败: $e';
      _fallbackToWebSocket(content, quote);
    }
  }

  /// 处理 SSE 事件（delta 打字机 / block·done 确认替换 / error 提示 / cold_war / user_message 回传）
  @visibleForTesting
  void handleStreamEvent(Map<String, dynamic> event) {
    final type = event['type'] as String?;
    switch (type) {
      case 'user_message':
        // 用户消息正式落库回传：替换本地临时 id
        _replaceTempUserMessage(event['data'] as Map<String, dynamic>?);
        notifyListeners();
      case 'delta':
        final text = event['text'] as String? ?? '';
        if (text.isNotEmpty) _appendStreamDelta(text);
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
        notifyListeners();
      case 'block':
        // 完整语义块（已落库）：确认替换当前流式气泡，开启下一气泡；语音逐句 TTS 入队顺序播放
        final msg = ChatMessage.fromJson(event);
        _confirmStreamBlock(msg);
        _enqueueTtsPlayback(msg);
      case 'done':
        _finishStreaming();
      case 'error':
        _error = event['detail'] as String?;
        // 服务端已内部回退非流式 chunked（仍会继续推 block/done），这里只提示，不重复发送
        notifyListeners();
      case 'cold_war':
        _messages.add(ChatMessage(
          id: DateTime.now().millisecondsSinceEpoch,
          sessionId: _sessionId ?? 0,
          senderType: 'system',
          isLocal: true,
          content: event['message'] as String? ?? 'TA 暂时没有回应你',
          createdAt: _api.serverNow().toIso8601String(),
        ));
        _finishStreaming();
    }
  }

  void _replaceTempUserMessage(Map<String, dynamic>? data) {
    if (data == null) return;
    final userMsg = ChatMessage.fromJson(data);
    final localIdx = _messages.lastIndexWhere((m) => m.isLocal && m.senderType == 'user');
    if (localIdx >= 0) {
      _messages[localIdx] = userMsg;
    } else {
      _messages.add(userMsg);
    }
  }

  void _appendStreamDelta(String text) {
    var sm = _streamingMessage;
    if (sm == null) {
      sm = ChatMessage(
        id: -DateTime.now().millisecondsSinceEpoch,
        sessionId: _sessionId ?? 0,
        senderType: 'ai',
        isLocal: true,
        content: text,
        createdAt: _api.serverNow().toIso8601String(),
      );
      _streamingMessage = sm;
      _messages.add(sm);
      notifyListeners();
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
    notifyListeners();
  }

  void _confirmStreamBlock(ChatMessage msg) {
    // P3-5（2026-08-29）：TTS 流式 consumer 中途死亡时，服务端走批量回退路径会全量重推 block
    // （P2-B 数据完整性兜底），此前已确认的块会被重复推送。按块 id 去重：messages 已含同一 id
    // 的正式块（非本地临时气泡）时不重复添加，避免出现重复气泡。
    if (msg.id > 0) {
      // V2-1：记录本轮已确认的正式块 ID（reset_blocks 据此只清当前轮）
      _streamingBlockIds.add(msg.id);
      final dupIdx = _messages.indexWhere((m) => m.id == msg.id && !(m.isLocal && m.id < 0));
      if (dupIdx >= 0) {
        _messages[dupIdx] = msg;
        notifyListeners();
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
      final leftover = _splitStreamLeftover(sm.content, msg.content);
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
    notifyListeners();
  }

  /// 若流式气泡正文以块正文为前缀，返回其剩余尾段；否则返回 ''（不拆分，回退旧行为）。
  String _splitStreamLeftover(String accumulated, String blockContent) {
    if (accumulated.isEmpty || blockContent.isEmpty) return '';
    if (!accumulated.startsWith(blockContent)) return '';
    return accumulated.substring(blockContent.length);
  }

  /// 把带 tts_url 的句块音频入队，供逐句顺序播放。
  void _enqueueTtsPlayback(ChatMessage msg) {
    final tts = msg.ttsMeta;
    final url = tts?['url'];
    if (url is String && url.isNotEmpty) {
      _voicePlayback.enqueue(url);
    }
  }

  /// 打断当前逐句语音播报（新一轮语音发出前调用，清空队列并停止当前句）。
  void _interruptVoicePlayback() {
    _voicePlayback.interrupt();
  }

  void _finishStreaming() {
    // V2-1：本轮结束（done/正常收尾）→ 清空本轮跟踪的正式块 ID，下轮 reset_blocks 不再误删历史。
    // 放在 `_streaming` 守卫之前：done 事件在流中途/收尾都会触发，_streaming 可能已是 false，
    // 但当前轮的跟踪集合都应在本轮结束后清空。
    _streamingBlockIds.clear();
    if (!_streaming) return;
    _streaming = false;
    // 移除空的流式占位气泡（若无 delta 即被打断，如 cold_war/异常）；有内容则保留为本地消息
    _messages.removeWhere((m) => m.isLocal && m.isAI && m.content.isEmpty && m.id < 0);
    _streamingMessage = null;
    _isTyping = false;
    notifyListeners();
  }

  /// SSE 传输层异常：回退非流式 WS 请求（用户消息已本地展示，不再重复添加）。
  void _fallbackToWebSocket(String content, Map<String, dynamic>? quote) {
    _streaming = false;
    _streamingMessage = null;
    // 移除空的流式气泡（无内容占位）
    _messages.removeWhere((m) => m.isLocal && m.isAI && m.content.isEmpty && m.id < 0);
    _isTyping = true;
    if (_currentCharacter != null) {
      _ws.send({
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

  /// 追加服务端返回的消息结果（首条 + 分块 AI 回复），按时间/ID 排序
  void _appendMessageResult(Map<String, dynamic> result, String messageKey) {
    final msg = result[messageKey] as Map<String, dynamic>;
    _messages.add(ChatMessage.fromJson(msg));
    for (final chunk in (result['chunks'] as List? ?? [])) {
      _messages.add(ChatMessage.fromJson(chunk as Map<String, dynamic>));
    }
    _messages.sort((a, b) {
      final c = a.createdAt.compareTo(b.createdAt);
      return c != 0 ? c : a.id.compareTo(b.id);
    });
  }

  Future<void> uploadImage(dynamic file, {String caption = ""}) async {
    if (_sessionId == null || _currentCharacter == null) return;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    try {
      final result = await _api.uploadChatImage(_sessionId!, file, userId: _userId, caption: caption, lang: _localeCode);
      _appendMessageResult(result, 'image_message');
    } catch (e) {
      _error = '图片发送失败: $e';
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
      _appendMessageResult(result, 'file_message');
    } catch (e) {
      _error = '文件发送失败: $e';
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
    _interruptVoicePlayback();
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
      _error = '语音发送失败: $e';
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
      _appendMessageResult(result, 'emoji_message');
    } catch (e) {
      _error = '表情发送失败: $e';
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
      _error = '删除失败: $e';
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


/// 待确认的 AI 能力调用（权限=每次询问 时由 WS permission_request 事件下发，2026-08-12）
class PendingPermissionRequest {
  final int actionId;
  final String scope;
  final String scopeLabel;
  final String prompt;
  final int characterId;

  const PendingPermissionRequest({
    required this.actionId,
    required this.scope,
    required this.scopeLabel,
    this.prompt = '',
    this.characterId = 0,
  });
}
