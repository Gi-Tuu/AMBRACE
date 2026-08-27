
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/features/chat/ws_handler.dart';
import 'package:ai_companion/models/message.dart';

void main() {
  group('typing delay (#63 机制2 前端)', () {
    test('SSE typing 事件置 typing，首块 delta 到达清除', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'typing', 'is_typing': true, 'delay': 2.5});
      expect(chat.isTyping, isTrue);

      // 首块内容到达 → 清除输入中
      chat.handleStreamEvent({'type': 'delta', 'text': '你好'});
      expect(chat.isTyping, isFalse);
    });

    test('SSE typing(false) 直接清除 typing', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'typing', 'is_typing': true});
      expect(chat.isTyping, isTrue);
      chat.handleStreamEvent({'type': 'typing', 'is_typing': false});
      expect(chat.isTyping, isFalse);
    });

    test('WS typing 事件置 typing，首块 ai_response 清除', () {
      var typing = false;
      final messages = <ChatMessage>[];
      final handler = WsHandler(
        onChanged: () {},
        getMessages: () => messages,
        sessionId: () => 1,
        serverNow: () => DateTime.utc(2026),
        userId: () => 1,
        characterId: () => 101,
        localeCode: () => 'zh',
        setTyping: (b) => typing = b,
        setError: (e) {},
        setPendingPermission: (p) {},
        rawSend: (p) {},
      );

      handler.handleWsMessage({'type': 'typing', 'is_typing': true, 'delay': 1.2});
      expect(typing, isTrue);

      handler.handleWsMessage({
        'type': 'ai_response',
        'data': {
          'id': 5,
          'session_id': 1,
          'sender_type': 'ai',
          'content': '你好',
          'created_at': '2026-08-24T00:00:00Z',
          'extra_meta': null,
        },
      });
      expect(typing, isFalse);
    });
  });
}
