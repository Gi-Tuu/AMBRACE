// XHR 四相位计时守卫（M1a-4，core 长期守卫）。
//
// 目的：在 VM 上用**手动假计时器**完全确定性驱动 `XhrUploadPhases`（生产适配器
// `_XhrIncrementalAdapter` 真实使用的同一实现），证明相位边界正确：
// - P1（send→上传完成）由 sendTimeout 约束，真实慢上行在此触发 sendTimeout；
// - P2（上传完成→headers）由 connectTimeout 约束，且**只在 P1 结束后起算**——
//   慢上行绝不被等待响应头的 connect 计时抢走；
// - P3（headers→DONE）由 receiveTimeout 作字节空闲计时；
// - `null`/`Duration.zero` 一律禁用；结算后不再起任何新计时器（不晚报超时）。
// 真实浏览器慢上行对照见交付 REPORT（探针），本文件只算契约/相位证据。

import 'dart:async';

import 'package:ambrace_core/src/api/upload_phase.dart';
import 'package:ambrace_core/src/attachment/upload_progress.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

/// 手动假计时器：记录创建时的时长，可被测试显式触发（不依赖真实时间）。
class _FakeTimer implements Timer {
  _FakeTimer(this.duration, this._cb);
  final Duration duration;
  final void Function() _cb;
  bool _active = true;

  @override
  int get tick => 0;

  @override
  bool get isActive => _active;

  @override
  void cancel() => _active = false;

  /// 显式触发（仅当仍 active，模拟真实 Timer 的单次语义）。
  void fire() {
    if (!_active) return;
    _active = false;
    _cb();
  }
}

/// 计时器工厂 + 记录表。
class _Clock {
  final List<_FakeTimer> timers = <_FakeTimer>[];

  Timer factory(Duration d, void Function() cb) {
    final t = _FakeTimer(d, cb);
    timers.add(t);
    return t;
  }

  List<_FakeTimer> get active => [for (final t in timers) if (t.isActive) t];
  Duration? get onlyActiveDuration =>
      active.length == 1 ? active.first.duration : null;
}

void main() {
  late _Clock clock;
  late List<DioExceptionType> fired;
  late bool settled;

  XhrUploadPhases build({
    Duration? send = const Duration(seconds: 5),
    Duration? connect = const Duration(seconds: 3),
    Duration? receive = const Duration(seconds: 2),
  }) {
    return XhrUploadPhases(
      sendTimeout: send,
      connectTimeout: connect,
      receiveTimeout: receive,
      isSettled: () => settled,
      onTimeout: (t) => fired.add(t),
      timerFactory: clock.factory,
    );
  }

  setUp(() {
    clock = _Clock();
    fired = <DioExceptionType>[];
    settled = false;
  });

  group('normalize：null / Duration.zero 一律禁用', () {
    test('null → null；Duration.zero → null；正数原样', () {
      expect(XhrUploadPhases.normalize(null), isNull);
      expect(XhrUploadPhases.normalize(Duration.zero), isNull);
      const d = Duration(milliseconds: 1500);
      expect(XhrUploadPhases.normalize(d), d);
    });

    test('全禁用时任何相位都不创建计时器', () {
      final p = build(send: null, connect: Duration.zero, receive: Duration.zero);
      p.beginSend();
      p.uploadCompleted();
      p.headersReceived();
      p.resetReceive();
      expect(clock.active, isEmpty);
      expect(fired, isEmpty);
      expect(p.sendBudget, isNull);
    });
  });

  group('P1：send → 上传完成（sendTimeout）', () {
    test('上传未完成时 sendTimeout 触发；期间不创建 connect 计时', () {
      final p = build();
      p.beginSend();
      expect(clock.onlyActiveDuration, const Duration(seconds: 5));
      expect(p.connectPhaseStarted, isFalse); // P2 未起
      clock.active.single.fire();
      expect(fired, [DioExceptionType.sendTimeout]);
    });

    test('uploadCompleted 清 send 计时并起 connect 计时；send 计时不再触发', () {
      final p = build();
      p.beginSend();
      final sendTimer = clock.timers.first;
      p.uploadCompleted();
      expect(sendTimer.isActive, isFalse, reason: '上传完成 ⇒ 清 send 计时（不晚报）');
      expect(p.connectPhaseStarted, isTrue);
      expect(clock.onlyActiveDuration, const Duration(seconds: 3));
      // 迟到的 send 触发必须无效
      sendTimer.fire();
      expect(fired, isEmpty);
      clock.active.single.fire();
      expect(fired, [DioExceptionType.connectionTimeout]);
    });

    test('uploadCompleted 幂等：只起一份 connect 计时', () {
      final p = build();
      p.beginSend();
      p.uploadCompleted();
      p.uploadCompleted(); // onload 与 onloadend 双触发
      expect(clock.active.length, 1);
    });

    test('uploadDone 后 beginSend 不再创建 send 计时', () {
      final p = build();
      p.uploadCompleted();
      final before = clock.timers.length;
      p.beginSend();
      expect(clock.timers.length, before);
    });

    test('bodyless（不 beginSend）：直接 uploadCompleted 起 connect', () {
      final p = build();
      p.uploadCompleted();
      expect(p.uploadDone, isTrue);
      expect(clock.onlyActiveDuration, const Duration(seconds: 3));
    });
  });

  group('P2→P3：headersReceived 换挡到接收空闲计时', () {
    test('headersReceived 清 connect 并起 receive 空闲', () {
      final p = build();
      p.beginSend();
      p.uploadCompleted();
      final connectTimer = clock.active.single;
      p.headersReceived();
      expect(connectTimer.isActive, isFalse);
      expect(clock.onlyActiveDuration, const Duration(seconds: 2));
      clock.active.single.fire();
      expect(fired, [DioExceptionType.receiveTimeout]);
    });

    test('resetReceive 重置为单个新计时（收字节即重置）', () {
      final p = build();
      p.headersReceived();
      final first = clock.active.single;
      p.resetReceive();
      expect(first.isActive, isFalse);
      expect(clock.active.length, 1);
      clock.active.single.fire();
      expect(fired, [DioExceptionType.receiveTimeout]);
    });

    test('receive 禁用（Duration.zero）时 headersReceived 不设空闲计时', () {
      final p = build(receive: Duration.zero);
      p.beginSend();
      p.uploadCompleted();
      p.headersReceived();
      expect(clock.active, isEmpty);
    });
  });

  group('结算：cancelAll 后不再起新计时（不晚报超时）', () {
    test('cancelAll 清空全部；settled 后 beginSend/resetReceive 均不创建', () {
      final p = build();
      p.beginSend();
      p.uploadCompleted();
      p.headersReceived();
      expect(clock.active.length, 1);
      settled = true;
      p.cancelAll();
      expect(clock.active, isEmpty);
      final n = clock.timers.length;
      p.beginSend();
      p.uploadCompleted();
      p.headersReceived();
      p.resetReceive();
      expect(clock.timers.length, n, reason: '结算后不得再起任何新计时器');
      expect(fired, isEmpty);
    });

    test('已触发的计时器不会二次触发', () {
      final p = build();
      p.beginSend();
      final t = clock.active.single;
      t.fire();
      t.fire();
      expect(fired, [DioExceptionType.sendTimeout]);
    });
  });

  group('wire 进度契约（AmbUploadProgress）', () {
    test('total<=0 表示未知；done 只代表上传阶段结束', () {
      const unknown = AmbUploadProgress(loaded: 10, total: 0, done: false);
      expect(unknown.total, 0);
      const done = AmbUploadProgress(loaded: 100, total: 100, done: true);
      expect(done.done, isTrue);
      expect(done.loaded, 100);
    });
  });
}
