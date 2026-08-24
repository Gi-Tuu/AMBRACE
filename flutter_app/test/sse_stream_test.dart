
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/services/api/chat_api.dart';

void main() {
  group('parseSseDataLine', () {
    test('解析合法 data: 行', () {
      final ev = parseSseDataLine('data: {"type":"delta","text":"你好"}');
      expect(ev, isNotNull);
      expect(ev!['type'], 'delta');
      expect(ev['text'], '你好');
    });

    test('忽略空行 / 非 data: 行 / 非法 JSON', () {
      expect(parseSseDataLine(''), isNull);
      expect(parseSseDataLine('  '), isNull);
      expect(parseSseDataLine('event: ping'), isNull);
      expect(parseSseDataLine('data: {bad json'), isNull);
      expect(parseSseDataLine('data:'), isNull);
    });

    test('兼容前后空白', () {
      final ev = parseSseDataLine('   data: {"type":"block","content":"你好。"}  ');
      expect(ev, isNotNull);
      expect(ev!['type'], 'block');
    });
  });

  group('ChatProvider.handleStreamEvent', () {
    test('delta 追加当前气泡（打字机），block 确认替换并开启下一气泡', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'delta', 'text': '你好'});
      expect(chat.messages.length, 1);
      expect(chat.messages.last.content, '你好');
      expect(chat.messages.last.isAI, isTrue);
      expect(chat.messages.last.isLocal, isTrue);

      chat.handleStreamEvent({'type': 'delta', 'text': '。'});
      expect(chat.messages.last.content, '你好。');

      // block 为完整落库块（含 id/created_at），确认替换当前流式气泡
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.last.id, 10);
      expect(chat.messages.last.isLocal, isFalse);
      expect(chat.messages.last.content, '你好。');

      // 下一 block 的 delta 开启新气泡
      chat.handleStreamEvent({'type': 'delta', 'text': '今天'});
      expect(chat.messages.last.content, '今天');
      expect(chat.messages.last.isLocal, isTrue);

      chat.handleStreamEvent({'type': 'done'});
      expect(chat.isStreaming, isFalse);
    });

    test('error 事件写入 error 且不中断（服务端已内部回退）', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'error', 'detail': '网络中断'});
      expect(chat.error, '网络中断');
    });

    test('cold_war 事件插入 system 消息', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'cold_war', 'message': 'TA 还在生闷气'});
      expect(chat.messages.any((m) => m.senderType == 'system'), isTrue);
    });
  });
}
