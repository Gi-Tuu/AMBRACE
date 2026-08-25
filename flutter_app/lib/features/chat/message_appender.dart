
// 纯逻辑：消息追加 / 去重 / 排序。无状态、不触 UI、不持有列表。
//
// 所有方法都就地修改调用方传入的 `messages` 列表（ChatProvider 的 `_messages` 引用），
// 或（`sorted`）返回新的排序列表。逻辑从 chat_provider.dart 的对应私有方法纯搬移，
// 与拆分前逐字节一致。
import '../../models/message.dart';

class MessageAppender {
  /// 用户消息正式落库回传（user_message 事件）：替换最后一条本地临时 user 消息。
  /// `data == null` 时 no-op。
  static void replaceTempUserMessage(List<ChatMessage> messages, Map<String, dynamic>? data) {
    if (data == null) return;
    final userMsg = ChatMessage.fromJson(data);
    final localIdx = messages.lastIndexWhere((m) => m.isLocal && m.senderType == 'user');
    if (localIdx >= 0) {
      messages[localIdx] = userMsg;
    } else {
      messages.add(userMsg);
    }
  }

  /// 追加服务端返回的消息结果（首条 + 分块 AI 回复），按时间/ID 排序。
  static void appendMessageResult(List<ChatMessage> messages, Map<String, dynamic> result, String messageKey) {
    final msg = result[messageKey] as Map<String, dynamic>;
    messages.add(ChatMessage.fromJson(msg));
    for (final chunk in (result['chunks'] as List? ?? [])) {
      messages.add(ChatMessage.fromJson(chunk as Map<String, dynamic>));
    }
    messages.sort((a, b) {
      final c = a.createdAt.compareTo(b.createdAt);
      return c != 0 ? c : a.id.compareTo(b.id);
    });
  }

  /// 按块 id 去重追加（P3-5）。若 `messages` 已含同一 id 的正式块（非本地临时气泡），
  /// 则原地替换该条目并返回 true；否则返回 false（调用方继续"流式气泡替换/拆尾段"逻辑）。
  static bool upsertConfirmedBlock(List<ChatMessage> messages, ChatMessage msg) {
    if (msg.id > 0) {
      final dupIdx = messages.indexWhere((m) => m.id == msg.id && !(m.isLocal && m.id < 0));
      if (dupIdx >= 0) {
        messages[dupIdx] = msg;
        return true;
      }
    }
    return false;
  }

  /// 移除空的流式占位气泡（无内容即被打断，如 cold_war/异常）。
  static void removeEmptyLocalBubbles(List<ChatMessage> messages) {
    messages.removeWhere((m) => m.isLocal && m.isAI && m.content.isEmpty && m.id < 0);
  }

  /// 排序（messages getter 复用）：按时间升序；时间相同则本地气泡优先，仍相同按 id 升序。
  static List<ChatMessage> sorted(List<ChatMessage> messages) =>
      List<ChatMessage>.from(messages)..sort((a, b) {
        final ta = DateTime.tryParse(a.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
        final tb = DateTime.tryParse(b.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
        int c = ta.compareTo(tb);
        if (c != 0) return c;
        if (a.isLocal != b.isLocal) return a.isLocal ? -1 : 1;
        return a.id.compareTo(b.id);
      });
}
