// 前后台心跳**载荷契约**的单元测试（A53 修订，2026-10-13）。
//
// 这里测的是「两端到底说同一种话」，不是重新实现一遍 ack：
// 编码/解析都调 `lib/services/foreground_ack.dart` 里生产在用的那两个函数，
// 最后两条再回到源码里证明**挂点真的存在**（发送侧 main.dart、接收侧 background_polling_service.dart
// 都经该 helper），否则 helper 写成神也无人调用。
//
// 旧 bug 的形状：发送侧 `lifecycleState?.name` 得到短名 `"resumed"`，接收侧比长名
// `"AppLifecycleState.resumed"` ⇒ 永不相等 ⇒ 回答时刻恒为 0 ⇒ 心跳信号从未生效。
import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/foreground_ack.dart';

/// 读生产源码（测试的工作目录可能是 flutter_app/，也可能是仓库根）。
String _readProdSource(String relative) {
  for (final base in [relative, 'flutter_app/$relative']) {
    final f = File(base);
    if (f.existsSync()) return f.readAsStringSync(encoding: utf8);
  }
  throw StateError('找不到生产源码 $relative（挂点测试必须在真实仓库目录下运行）');
}

void main() {
  group('编码侧 foregroundAckPayload（main.dart 真实调用的那个）', () {
    test('只有 resumed 编码出 resumed=true', () {
      expect(foregroundAckPayload(AppLifecycleState.resumed), {kForegroundResumedKey: true});
    });

    test('inactive/hidden/paused/detached/null 全部编码出 false（不发字符串协议）', () {
      for (final state in [
        AppLifecycleState.inactive,
        AppLifecycleState.hidden,
        AppLifecycleState.paused,
        AppLifecycleState.detached,
        null,
      ]) {
        expect(foregroundAckPayload(state), {kForegroundResumedKey: false}, reason: '$state');
      }
    });

    test('载荷里不再出现生命周期事件字符串（回归护栏：短名/长名之争到此为止）', () {
      final payload = foregroundAckPayload(AppLifecycleState.resumed);
      expect(payload.keys, [kForegroundResumedKey]);
      expect(payload.values.whereType<String>(), isEmpty);
    });
  });

  group('解析侧 isResumedAck（background_polling_service.dart 真实调用的那个）', () {
    test('bool true 是唯一放行值', () {
      expect(isResumedAck({kForegroundResumedKey: true}), isTrue);
    });

    test('fail-closed：false/null/字符串/数字/缺键/非 Map 一律不认', () {
      for (final payload in [
        {kForegroundResumedKey: false},
        {kForegroundResumedKey: null},
        <String, Object>{}, // 缺键
        {kForegroundResumedKey: 'true'}, // 字符串真值
        {kForegroundResumedKey: 'True'},
        {kForegroundResumedKey: 1}, // 数字
        {kForegroundResumedKey: 1.0},
        {'state': 'resumed'}, // 旧契约载荷：新解析器必须拒绝
        {'state': 'AppLifecycleState.resumed'},
      ]) {
        expect(isResumedAck(payload), isFalse, reason: '$payload');
      }
      for (final payload in [null, '', 'resumed', 0, 1, <Object>[], [true]]) {
        expect(isResumedAck(payload), isFalse, reason: '$payload');
      }
    });
  });

  group('端到端：同一 helper 编 → 解', () {
    test('resumed 往返为 true，其余状态往返为 false', () {
      expect(
        isResumedAck(foregroundAckPayload(AppLifecycleState.resumed)),
        isTrue,
      );
      for (final state in [
        AppLifecycleState.inactive,
        AppLifecycleState.hidden,
        AppLifecycleState.paused,
        AppLifecycleState.detached,
        null,
      ]) {
        expect(isResumedAck(foregroundAckPayload(state)), isFalse, reason: '$state');
      }
    });
  });

  group('挂点证明：生产两端真的调用了这个 helper', () {
    test('发送侧 main.dart 用 helper 编码并 invoke 契约事件', () {
      final src = _readProdSource('lib/main.dart');
      expect(src, contains("import 'services/foreground_ack.dart'"));
      expect(src, contains('foregroundAckPayload(WidgetsBinding.instance.lifecycleState)'));
      expect(src, contains('invoke(kForegroundAckEvent, payload)'));
      expect(src, contains('on(kForegroundProbeEvent)'));
      // 旧的字符串协议必须彻底消失（留着就是第二个真相源）
      expect(src, isNot(contains('lifecycleState?.name')));
      expect(src, isNot(contains('"state": state')));
    });

    test('接收侧 background_polling_service.dart 用 helper 解析并探测契约事件', () {
      final src = _readProdSource('lib/services/background_polling_service.dart');
      expect(src, contains('import "foreground_ack.dart";'));
      expect(src, contains('service.on(kForegroundAckEvent).listen'));
      expect(src, contains('if (isResumedAck(data)) {'));
      expect(src, contains('service.invoke(kForegroundProbeEvent);'));
      // 长名比较是本次 bug 本体，必须绝迹
      expect(src, isNot(contains('"AppLifecycleState.resumed"')));
    });

    test('事件名常量与改造前逐字一致（只换载荷，不换通道）', () {
      expect(kForegroundProbeEvent, 'fgProbe');
      expect(kForegroundAckEvent, 'fgAck');
    });
  });
}
