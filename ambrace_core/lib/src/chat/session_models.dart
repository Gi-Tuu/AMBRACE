/// 会话工作台（M1a-3）的只读 wire DTO：`GET /api/v1/chat/sessions` 的列表项
/// 与 `GET /api/v1/chat/sessions/{sid}/messages` 的分页信封。
///
/// 纪律：
/// - 只解析后端**真实返回**的字段（契约权威＝`backend/app/api/chat.py` 与
///   `app/schemas/chat.py`），不伪造接口没有的字段（如「最新消息预览」）；
/// - 必填字段（id / character_id / session_id）缺失或类型错误 ⇒ 抛 [FormatException]，
///   由调用方归一为 ApiException（坏响应不得伪装成「成功的空列表」）；
/// - 时间字段原样保留（后端 UTC naive / ISO 串），格式化交给展示层；
/// - 纯解析、零 IO、不 import flutter / dart:io。
library;

import '../models/chat_message.dart';

int _reqInt(Map<String, dynamic> j, String key, String what) {
  final v = j[key];
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) {
    final p = int.tryParse(v);
    if (p != null) return p;
  }
  throw FormatException('malformed $what: missing $key');
}

int? _asIntN(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) return int.tryParse(v);
  return null;
}

String _asStr(dynamic v) => v is String ? v : (v == null ? '' : '$v');

String? _asStrN(dynamic v) => v is String ? v : null;

bool _asBool(dynamic v, [bool fallback = false]) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  if (v is String) return v.toLowerCase() == 'true';
  return fallback;
}

/// 会话摘要（`GET /api/v1/chat/sessions` 列表项）：只取工作台需要的展示/归属字段。
/// [title] 可空＝用户未命名（展示层用双语占位，**不**伪造标题）。
class ChatSessionSummary {
  final int id;
  final int characterId;
  final String? title;
  final bool isActive;
  final String createdAt; // 后端 UTC naive / ISO 原文
  final String updatedAt;

  const ChatSessionSummary({
    required this.id,
    required this.characterId,
    this.title,
    this.isActive = true,
    this.createdAt = '',
    this.updatedAt = '',
  });

  factory ChatSessionSummary.fromJson(Map<String, dynamic> j) => ChatSessionSummary(
        id: _reqInt(j, 'id', 'chat session'),
        characterId: _reqInt(j, 'character_id', 'chat session'),
        title: _asStrN(j['title']),
        isActive: _asBool(j['is_active'], true),
        createdAt: _asStr(j['created_at']),
        updatedAt: _asStr(j['updated_at']),
      );
}

/// 历史分页信封（`GET /api/v1/chat/sessions/{sid}/messages`）：
/// [messages] 为**窗口内时间正序**，[total] 为该会话消息总数（真分页口径）。
/// 既有 `ChatTransport.getMessages` 只返回 List、丢了 total —— 本类型承接 total，
/// 不改旧方法返回类型（旧调用方逐行为不变）。
class ChatHistoryPage {
  final int sessionId;
  final List<ChatMessage> messages;
  final int total;

  const ChatHistoryPage({required this.sessionId, required this.messages, required this.total});
}

/// 从响应体解析历史分页信封；封套字段缺失/类型错 ⇒ [FormatException]。
ChatHistoryPage chatHistoryPageFromJson(Map<String, dynamic> j) {
  final sid = _reqInt(j, 'session_id', 'chat history');
  final raw = j['messages'];
  if (raw is! List) {
    throw FormatException('malformed chat history: expected messages list, got ${raw.runtimeType}');
  }
  final items = <ChatMessage>[
    for (final m in raw)
      if (m is Map) ChatMessage.fromJson(m.cast<String, dynamic>()),
  ];
  final total = _asIntN(j['total']) ?? items.length;
  return ChatHistoryPage(sessionId: sid, messages: items, total: total);
}
