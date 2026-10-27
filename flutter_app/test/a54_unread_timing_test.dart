// A54（2026-10-11）一期：红点时机与抑制规则的守卫测试。
//
// 分两类，各自说明「测得到什么、测不到什么」：
// ① ReadSuppressor 的纯规则（TTL／计数变化／归零）——真单测，规则错就是红；
// ② 顺序与接线的源码守卫（仿 test_no_double_notify_a39 的先例）——
//    钉「吸收排在应用快照之前」和「AI 回复落地真的接到了标记已读」，
//    防的是后来者把这两处改回去（这两件事在 widget 测试里靠 mock 网络很难稳定复现）。
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/unread_engine.dart';

bool _hasActiveCharacterReadBinding(String source) {
  return source.replaceAll('\r\n', '\n').contains(
    'markRead(_sessionId!,\n            characterId: _currentCharacter?.id);',
  );
}

void main() {
  for (final eol in ['\n', '\r\n']) {
    test('character read binding survives ${eol.length}-byte newline without losing guards', () {
      final source = 'markRead(_sessionId!,$eol            characterId: _currentCharacter?.id);';
      expect(_hasActiveCharacterReadBinding(source), isTrue);
      expect(_hasActiveCharacterReadBinding(source.replaceFirst(
        'characterId: _currentCharacter?.id', 'characterId: null')), isFalse);
      expect(_hasActiveCharacterReadBinding(source.replaceFirst(
        'characterId: _currentCharacter?.id', 'characterId: unrelatedId')), isFalse);
      expect(_hasActiveCharacterReadBinding('markRead(_sessionId!);'), isFalse);
    });
  }

  group('ReadSuppressor（抑制带 TTL，不再无限期压住红点）', () {
    test('刚记下的同一 count 被压住；计数一变（真有新消息）立刻解除', () {
      var now = 1000;
      final s = ReadSuppressor(nowMs: () => now);
      s.note(7, 3);
      expect(s.isSuppressed(7, 3), isTrue);
      expect(s.filter({7: 3, 8: 1}), {8: 1}); // 别的角色不受影响
      now += 1;
      s.note(7, 3);
      expect(s.isSuppressed(7, 4), isFalse, reason: '涨 1 条＝真有新消息，必须露出来');
      expect(s.isSuppressed(7, 0), isFalse, reason: '服务器已清零＝不必再压');
    });

    test('TTL 到期后自动交回服务器事实（不会永久压住红点）', () {
      var now = 0;
      final s = ReadSuppressor(
        ttl: const Duration(seconds: 30),
        nowMs: () => now,
      );
      s.note(7, 3);
      now = 29000;
      expect(s.isSuppressed(7, 3), isTrue);
      now = 31000;
      expect(s.isSuppressed(7, 3), isFalse,
          reason: '超过 TTL 还压着＝红点可能永远不出现（旧形状无 TTL 就是这个风险）');
    });

    test('没记过的角色不受影响；解除是单向的（查询本身会清掉失效抑制）', () {
      final s = ReadSuppressor(nowMs: () => 5000);
      expect(s.isSuppressed(9, 2), isFalse);
      s.note(9, 2);
      expect(s.isSuppressed(9, 2), isTrue);
      expect(s.isSuppressed(9, 5), isFalse); // 计数变了＝真有新消息 ⇒ 解除
      expect(s.isSuppressed(9, 2), isFalse,
          reason: '解除后不会靠旧记录复活成「压住」；要再压必须重新 note（＝真的又读到一次）');
      s.note(9, 2);
      expect(s.isSuppressed(9, 2), isTrue);
    });
  });

  group('顺序与接线（源码守卫）', () {
    test('读快照：先吸收新增事件，后应用快照（消掉「先亮后灭」）', () {
      final src = File('lib/services/notification_service.dart').readAsStringSync();
      final absorb = src.indexOf('1) 先消费新增未读事件');
      final apply = src.indexOf('2) 再应用快照');
      expect(absorb, greaterThan(0), reason: '吸收段被改名或删掉了，顺序守卫失效');
      expect(apply, greaterThan(absorb),
          reason: '顺序回退成「先 _applySnapshot 后吸收」＝红点亮一下又灭（A54 要修的那条）');
      expect(src.contains('await prefs.reload();'), isTrue,
          reason: '不 reload 就读不到后台 isolate 写的快照，5 秒通道等于空转');
    });

    test('标记已读后走网络权威校正，不只看本地快照', () {
      final src = File('lib/services/notification_service.dart').readAsStringSync();
      final m = src.indexOf('Future<void> markRead(');
      final body = src.substring(m, m + 1200);
      expect(body.contains('await _fallbackPoll();'), isTrue,
          reason: '标完已读只刷本地快照＝服务器清了也还亮着');
    });

    test('聊天页进会话时把角色传进去（修「清红点恒不生效」那条死路径）', () {
      final prov = File('lib/providers/chat_provider.dart').readAsStringSync();
      expect(_hasActiveCharacterReadBinding(prov),
          isTrue,
          reason: '不传 characterId 就又回到「靠 _sessionCharMap 反查、而那张表只有吸收路径会写」');
    });

    test('两条回复通道（SSE／WS）都接到了「回复落地即标已读」', () {
      final sse = File('lib/features/chat/stream_handler.dart').readAsStringSync();
      final ws = File('lib/features/chat/ws_handler.dart').readAsStringSync();
      final prov = File('lib/providers/chat_provider.dart').readAsStringSync();
      expect(sse.contains('_onReplySettled?.call();'), isTrue);
      expect(ws.contains('_onReplySettled?.call();'), isTrue);
      expect(prov.split('onReplySettled: _noteAiReplyVisible').length - 1, 2,
          reason: '只接一条腿＝另一条路径回来的回复照样要等轮询才清红点');
      // 可见性判据只有一处：服务里问「是不是正看着这个角色」，provider 不自己猜
      final ns = File('lib/services/notification_service.dart').readAsStringSync();
      expect(ns.contains('Future<void> onVisibleReply('), isTrue);
      expect(ns.indexOf('_chatCharacterId != charId') > 0, isTrue);
    });
  });
}
