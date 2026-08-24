import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:ai_companion/services/background_ws_client.dart';

void main() {
  group('ReconnectBackoff 指数退避', () {
    test('1s 起倍增，上限 60s', () {
      const b = ReconnectBackoff();
      expect(b.delayFor(0), const Duration(seconds: 1));
      expect(b.delayFor(1), const Duration(seconds: 2));
      expect(b.delayFor(2), const Duration(seconds: 4));
      expect(b.delayFor(3), const Duration(seconds: 8));
      expect(b.delayFor(5), const Duration(seconds: 32));
      expect(b.delayFor(6), const Duration(seconds: 60)); // 64 → 封顶 60
      expect(b.delayFor(100), const Duration(seconds: 60));
    });

    test('自定义 base/max', () {
      const b = ReconnectBackoff(baseSeconds: 2, maxSeconds: 10);
      expect(b.delayFor(0), const Duration(seconds: 2));
      expect(b.delayFor(1), const Duration(seconds: 4));
      expect(b.delayFor(3), const Duration(seconds: 10)); // 16 → 封顶 10
    });
  });

  group('NotifyEvent.tryParse', () {
    test('解析主动消息事件', () {
      final ev = NotifyEvent.tryParse({
        'type': 'ai_response',
        'data': {
          'session_id': 7,
          'character_id': 3,
          'sender_type': 'ai',
          'content': '今天过得怎么样？',
        },
        'is_proactive': true,
      });
      expect(ev, isNotNull);
      expect(ev!.characterId, 3);
      expect(ev.sessionId, 7);
      expect(ev.content, '今天过得怎么样？');
      expect(ev.isProactive, isTrue);
    });

    test('解析新消息事件（非主动）', () {
      final ev = NotifyEvent.tryParse({
        'type': 'ai_response',
        'data': {'character_id': 9, 'content': '嗯嗯'},
      });
      expect(ev, isNotNull);
      expect(ev!.characterId, 9);
      expect(ev.isProactive, isFalse);
    });

    test('缺 character_id 返回 null', () {
      expect(NotifyEvent.tryParse({'data': {'content': 'x'}}), isNull);
    });

    test('非 map 返回 null', () {
      expect(NotifyEvent.tryParse('x'), isNull);
      expect(NotifyEvent.tryParse(null), isNull);
      expect(NotifyEvent.tryParse({'data': []}), isNull);
    });
  });

  group('EventWsClient 重连', () {
    test('连接失败按退避重连，达到上限后停止', () {
      final delays = <int>[];
      var connects = 0;

      WebSocketChannel failingConnector(Uri uri) {
        connects++;
        throw Exception('boom');
      }

      final client = EventWsClient(
        uriBuilder: () => Uri.parse('ws://127.0.0.1/notifications/ws'),
        onEvent: (_) {},
        connect: failingConnector,
        maxReconnectAttempts: 3,
        timerFactory: (Duration d, void Function() cb) {
          delays.add(d.inSeconds);
          cb();
          return Timer(Duration.zero, () {});
        },
      );

      client.start();
      // start 1 次 + 重连 3 次 = 4 次连接尝试；退避序列 1s,2s,4s
      expect(connects, 4);
      expect(delays, [1, 2, 4]);
      client.dispose();
    });
  });
}
