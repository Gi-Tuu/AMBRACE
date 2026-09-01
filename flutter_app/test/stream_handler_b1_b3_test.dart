import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/chat/stream_handler.dart';
import 'package:ai_companion/models/message.dart';

/// B1/B3 回归测试（2026-09-01 审查）：
/// - B1：SSE 的 user_message 是扁平结构（无 data 嵌套），WS 是 {"data":{...}}——
///   两种形状都必须把本地负 id 临时气泡替换为正式 id（此前 SSE 主链路恒 no-op）；
/// - B3：abortStreaming 必须移除本轮已确认上屏的正式块（否则 WS 回退后同轮双回复）。
void main() {
  List<ChatMessage> messages = <ChatMessage>[];
  var changed = 0;

  StreamHandler makeHandler() {
    changed = 0;
    messages = <ChatMessage>[]; // 每用例独立消息列表
    return StreamHandler(
      onChanged: () => changed++,
      getMessages: () => messages,
      sessionId: () => 7,
      serverNow: () => DateTime.utc(2026, 9, 1, 12),
      interruptVoicePlayback: () {},
      enqueueTts: (_) {},
      setError: (_) {},
      setTyping: (_) {},
    );
  }

  void seedLocalUserBubble() {
    messages.add(ChatMessage(
      id: -DateTime.now().millisecondsSinceEpoch,
      sessionId: 7,
      senderType: 'user',
      isLocal: true,
      content: '在干嘛呢',
      createdAt: '2026-09-01T12:00:00Z',
    ));
  }

  test('B1 扁平事件（SSE 展平形状）替换本地临时 id', () {
    final h = makeHandler();
    seedLocalUserBubble();
    h.handleEvent({
      'type': 'user_message',
      'id': 12345,
      'session_id': 7,
      'sender_type': 'user',
      'content': '在干嘛呢',
      'created_at': '2026-09-01T12:00:01Z',
    });
    expect(messages, hasLength(1));
    expect(messages.first.id, 12345, reason: 'SSE 扁平形状也必须替换负临时 id');
    expect(messages.first.isLocal, isFalse);
  });

  test('B1 嵌套事件（WS 形状）替换本地临时 id（兼容不回归）', () {
    final h = makeHandler();
    seedLocalUserBubble();
    h.handleEvent({
      'type': 'user_message',
      'data': {
        'id': 12346,
        'session_id': 7,
        'sender_type': 'user',
        'content': '在干嘛呢',
        'created_at': '2026-09-01T12:00:01Z',
      },
    });
    expect(messages, hasLength(1));
    expect(messages.first.id, 12346);
  });

  test('B3 abortStreaming 移除本轮已确认正式块', () {
    final h = makeHandler();
    h.startStream();
    // 用户气泡 + 一条已确认的正式块（半截回复已上屏）
    seedLocalUserBubble();
    h.handleEvent({
      'type': 'block',
      'id': 777,
      'session_id': 7,
      'sender_type': 'ai',
      'content': '刚躺下刷手机呢，',
      'created_at': '2026-09-01T12:00:05Z',
    });
    expect(messages.any((m) => m.id == 777), isTrue);

    h.abortStreaming();
    expect(messages.any((m) => m.id == 777), isFalse,
        reason: '正式块必须随 abort 移除，否则 WS 回退后同轮两条回复');
    // 用户气泡与流式占位清理照旧
    expect(messages.every((m) => m.senderType != 'ai' || m.id == 777), isTrue);
  });

  test('B3 abortStreaming 不影响历史消息（只清本轮块）', () {
    final h = makeHandler();
    // 上一轮的历史 AI 消息（不在 _streamingBlockIds 内）
    messages.add(ChatMessage(
      id: 900, sessionId: 7, senderType: 'ai', isLocal: false,
      content: '上一轮回复', createdAt: '2026-09-01T11:00:00Z',
    ));
    h.startStream();
    h.handleEvent({
      'type': 'block',
      'id': 777, 'session_id': 7, 'sender_type': 'ai',
      'content': '本轮半截', 'created_at': '2026-09-01T12:00:05Z',
    });
    h.abortStreaming();
    expect(messages.any((m) => m.id == 900), isTrue, reason: '历史消息不得误删');
    expect(messages.any((m) => m.id == 777), isFalse);
  });
}
