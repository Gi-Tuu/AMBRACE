import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/voice_vad.dart';

/// 客户端能量 VAD 状态机单测：说话开始/结束（静音截断）、噪声不误触发、强制结束。
void main() {
  group('VadGate 能量端点检测', () {
    test('安静环境（低于阈值）不触发 speechStart', () {
      final gate = VadGate(speechThresholdDb: -45, speechStartHoldMs: 150, silenceMs: 700);
      VadEvent evt = const VadEvent(VadEventType.none);
      for (var t = 0; t < 3000; t += 200) {
        evt = gate.feed(-160, t);
      }
      expect(evt.type, VadEventType.none);
      expect(gate.isSpeech, isFalse);
    });

    test('持续说话跨过 hold 判定 speechStart；静音超时判定 speechEnd', () {
      final gate = VadGate(speechThresholdDb: -45, speechStartHoldMs: 150, silenceMs: 700);
      // 说话（-20 > -45）
      VadEvent? evt;
      var t = 0;
      for (; t < 400; t += 200) {
        evt = gate.feed(-20, t); // 0,200
      }
      expect(evt!.isSpeechStart, isTrue, reason: '1000ms(含 hold)应触发 speechStart');
      // 继续说话，保持 speech（最后一次活跃在 t=800）
      for (; t < 1000; t += 200) {
        evt = gate.feed(-15, t);
      }
      expect(evt!.type, VadEventType.none);
      expect(gate.isSpeech, isTrue);
      // 静音超过 silenceMs（最后一次活跃 800ms 后 ≥700ms → 在 1600ms 判定）→ speechEnd
      evt = gate.feed(-160, 1600);
      expect(evt.isSpeechEnd, isTrue, reason: '静音应判定 speechEnd');
      expect(gate.isSpeech, isFalse);
    });

    test('单次噪声尖峰不误触发（未持续 hold），回到等待', () {
      final gate = VadGate(speechThresholdDb: -45, speechStartHoldMs: 150, silenceMs: 700);
      // 一次超过阈值的采样后立刻安静，不应触发 speechStart
      var evt = gate.feed(-20, 0);
      expect(evt.type, VadEventType.none);
      evt = gate.feed(-160, 200);
      expect(evt.type, VadEventType.none);
      expect(gate.isSpeech, isFalse);
    });

    test('超长说话强制截断（maxSegmentMs）', () {
      final gate = VadGate(speechThresholdDb: -45, speechStartHoldMs: 150, silenceMs: 700, maxSegmentMs: 1000);
      var t = 0;
      VadEvent evt = const VadEvent(VadEventType.none);
      for (; t < 2000; t += 200) {
        evt = gate.feed(-20, t);
        if (evt.isSpeechEnd) break;
      }
      expect(evt.isSpeechEnd, isTrue, reason: '超长应在 maxSegmentMs 处被强制截断');
      expect(gate.isSpeech, isFalse);
    });

    test('forceEnd 在说话中强制结束并返回 speechEnd', () {
      final gate = VadGate();
      // 进入 speech
      gate.feed(-20, 0);
      gate.feed(-20, 200);
      expect(gate.isSpeech, isTrue);
      final evt = gate.forceEnd(600);
      expect(evt, isNotNull);
      expect(evt!.isSpeechEnd, isTrue);
      expect(gate.isSpeech, isFalse);
      // 非说话中 forceEnd 返回 null
      expect(gate.forceEnd(800), isNull);
    });

    test('自然静音截断时 startMs/endMs 不被 reset 清零', () {
      final gate = VadGate(speechThresholdDb: -45, speechStartHoldMs: 150, silenceMs: 700);
      gate.feed(-20, 100);                  // 候选起点 @100
      final startEvt = gate.feed(-20, 300); // 200ms >= hold -> speechStart，起点 100
      expect(startEvt.isSpeechStart, isTrue);
      expect(startEvt.startMs, 100);
      gate.feed(-20, 500);                  // 最后一次活跃 @500
      final endEvt = gate.feed(-160, 1300); // 静音 800 >= 700 -> speechEnd
      expect(endEvt.isSpeechEnd, isTrue);
      expect(endEvt.startMs, 100, reason: '起点应为首次 speechStart 的 100ms');
      expect(endEvt.endMs, 500, reason: '终点应为最后活跃时刻 500ms');
    });
  });
}
