
import '../../models/message.dart';
import 'message_appender.dart';

/// 待确认的 AI 能力调用（权限=每次询问 时由 WS permission_request 事件下发，2026-08-12）。
/// 由 ChatProvider 持有（`pendingPermission`）；此处定义并由 ChatProvider 转发导出。
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

/// 处理 WebSocket 推送（收：user_message / ai_response / cold_war / typing / error /
/// permission_request）与 WS 消息发送（发：普通 / 批量 / 继续）。
///
/// 通过构造函数注入 ChatProvider 的状态/变更回调，不反向依赖 ChatProvider。
/// `permission_request` 解析成 [PendingPermissionRequest] 后交给 ChatProvider 持有
///（`_pendingPermission` 归属 ChatProvider）；本类不持有权限状态。
class WsHandler {
  WsHandler({
    required void Function() onChanged,
    required List<ChatMessage> Function() getMessages,
    required int? Function() sessionId,
    required DateTime Function() serverNow,
    required int Function() userId,
    required int? Function() characterId,
    required String Function() localeCode,
    required void Function(bool) setTyping,
    required void Function(String?) setError,
    required void Function(PendingPermissionRequest?) setPendingPermission,
    required void Function(Map<String, dynamic>) rawSend,
  })  : _onChanged = onChanged,
        _getMessages = getMessages,
        _sessionId = sessionId,
        _serverNow = serverNow,
        _userId = userId,
        _characterId = characterId,
        _localeCode = localeCode,
        _setTyping = setTyping,
        _setError = setError,
        _setPendingPermission = setPendingPermission,
        _rawSend = rawSend;

  final void Function() _onChanged;
  final List<ChatMessage> Function() _getMessages;
  final int? Function() _sessionId;
  final DateTime Function() _serverNow;
  final int Function() _userId;
  final int? Function() _characterId;
  final String Function() _localeCode;
  final void Function(bool) _setTyping;
  final void Function(String?) _setError;
  final void Function(PendingPermissionRequest?) _setPendingPermission;
  final void Function(Map<String, dynamic>) _rawSend;

  List<ChatMessage> get _messages => _getMessages();

  /// WS 消息入口（等价原 ChatProvider._onWsMessage）。
  void handleWsMessage(Map<String, dynamic> data) {
    final type = data['type'] as String?;
    switch (type) {
      case 'user_message':
        // 用户消息正式落库回传：替换本地临时 id（保证删除可用；2026-08-15）
        MessageAppender.replaceTempUserMessage(_messages, data['data'] as Map<String, dynamic>?);
        _onChanged();
      case 'ai_response':
        final msgData = data['data'] as Map<String, dynamic>;
        // 只接收当前会话的消息：其他会话的主动消息（如另一个角色的私信）不得显示/插入本会话
        if (msgData['session_id'] != null && msgData['session_id'] != _sessionId()) {
          return;
        }
        _messages.add(ChatMessage.fromJson(msgData));
        _setTyping(false);
        _onChanged();
      case 'cold_war':
        // 冷战拦截（v3）：角色生气冷战期不回复，本地显示系统提示
        _messages.add(ChatMessage(
          id: DateTime.now().millisecondsSinceEpoch,
          sessionId: _sessionId() ?? 0,
          senderType: 'system',
          isLocal: true,
          content: data['message'] as String? ?? 'TA 暂时没有回应你',
          createdAt: _serverNow().toIso8601String(),
        ));
        _setTyping(false);
        _onChanged();
      case 'typing':
        _setTyping(data['is_typing'] as bool? ?? false);
        _onChanged();
      case 'error':
        _setError(data['detail'] as String?);
        _setTyping(false);
        _onChanged();
      case 'permission_request':
        final pd = data['data'] as Map<String, dynamic>?;
        if (pd != null) {
          _setPendingPermission(PendingPermissionRequest(
            actionId: pd['action_id'] as int? ?? 0,
            scope: pd['scope'] as String? ?? '',
            scopeLabel: pd['scope_label'] as String? ?? '能力',
            prompt: pd['prompt'] as String? ?? '',
            characterId: pd['character_id'] as int? ?? 0,
          ));
          _onChanged();
        }
    }
  }

  /// 透传发送任意 WS 消息（等价原 `_ws.send`）。
  void sendMessage(Map<String, dynamic> payload) {
    _rawSend(payload);
  }

  /// 批量发送：batch_messages（等价原 `_sendBatch` 里的 ws.send 部分）。
  void sendBatch(List<String> texts) {
    final cid = _characterId();
    if (cid == null) return;
    _rawSend({
      "type": "batch_messages",
      "character_id": cid,
      "messages": texts,
      "user_id": _userId(),
      "lang": _localeCode(),
    });
  }

  /// 继续发送：continue_chat。
  void sendContinue({required int characterId, required int lastMessageId}) {
    _rawSend({
      'type': 'continue_chat',
      'character_id': characterId,
      'last_message_id': lastMessageId,
      'user_id': _userId(),
      'lang': _localeCode(),
    });
  }
}
