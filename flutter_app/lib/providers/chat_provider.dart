
import 'package:flutter/foundation.dart';
import '../models/message.dart';
import '../models/character.dart';
import '../services/api_client.dart';
import '../services/notification_service.dart';
import '../services/api/permission_api.dart';
import '../services/websocket_service.dart';

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
  PendingPermissionRequest? _pendingPermission;
  PendingPermissionRequest? get pendingPermission => _pendingPermission;

  ChatProvider();

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
    // 通过 WebSocket 发送
    _ws.send({
      'type': 'chat_message',
      'character_id': _currentCharacter!.id,
      'content': content,
      'user_id': _userId,
      'lang': _localeCode,
      if (quote != null) 'quote': quote,
    });
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

  /// 上传语音消息：HTTP 上传 → 后端 ASR 转写 → 加入语音消息与 AI 回复。
  Future<bool> uploadVoice(dynamic file, {int durationSec = 0, String lang = 'zh'}) async {
    if (_sessionId == null || _currentCharacter == null) return false;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    var ok = false;
    try {
      final result = await _api.uploadChatVoice(_sessionId!, file, durationSec: durationSec, lang: lang);
      _appendMessageResult(result, 'voice_message');
      ok = true;
    } catch (e) {
      _error = '语音发送失败: $e';
    }
    _isSending = false;
    _isTyping = false;
    notifyListeners();
    return ok;
  }

  /// 发送自定义表情消息：引用已上传表情图，AI 经表情名理解
  Future<void> sendEmoji(String emojiUrl, String name) async {
    if (_sessionId == null || _currentCharacter == null) return;
    _isSending = true;
    _isTyping = true;
    notifyListeners();
    try {
      final result = await _api.sendEmojiMessage(_sessionId!, emojiUrl, name, lang: _localeCode);
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
