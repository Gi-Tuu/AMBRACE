// 前后台判定纯规则的单元测试（A53，2026-10-11）。
//
// 这几条测的是「改完到底是不是 fail-closed」：
// 关键是第 3 条——**回答一过期就必须判后台**。旧形状之所以会「一退后台就都不弹」，
// 是因为标志只会等一条消息、消息丢了就永久停在 true；新形状靠回答过期把状态拉回来。
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/foreground_state.dart';

void main() {
  group('foregroundFromSignals', () {
    test('生命周期说后台 ⇒ 判后台（不看心跳）', () {
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: false,
          nowMs: 1000,
          lastResumedAckMs: 1000,
        ),
        isFalse,
      );
    });

    test('生命周期说前台且刚回答过 ⇒ 判前台', () {
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 20000,
          lastResumedAckMs: 18000,
        ),
        isTrue,
      );
    });

    test('回答一过期就判后台（ onHide 消息丢了也能自愈）', () {
      // 生命周期标志仍为 true（那条 onHide 推送没送到），但回答已经**超过**窗口没更新
      // ⇒ 判后台，该弹就弹。这一条是本次修复的正题，摘掉「回答过期」就退化成旧 bug。
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 11001, // 距上次回答 10001ms ＞ 窗口 10000ms
          lastResumedAckMs: 1000,
        ),
        isFalse,
      );
      // 窗口边界是**含**的：恰好 10000ms 仍算前台（容忍一次调度抖动，不至于每次抖动就误弹）
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 11000,
          lastResumedAckMs: 1000,
        ),
        isTrue,
      );
    });

    test('从没回答过（开机自启时主 isolate 还没起）⇒ 判后台', () {
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 5000,
          lastResumedAckMs: 0,
        ),
        isFalse,
      );
    });

    test('窗口边界：恰好 windowMs 仍算前台，再多 1ms 判后台', () {
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 10000,
          lastResumedAckMs: 0,
          windowMs: 10000,
        ),
        isFalse, // lastResumedAckMs=0 走「从没回答过」那一支
      );
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 10000,
          lastResumedAckMs: 0 + 1,
          windowMs: 9999,
        ),
        isTrue,
      );
      expect(
        foregroundFromSignals(
          lifecycleSaysForeground: true,
          nowMs: 10000,
          lastResumedAckMs: 0,
          windowMs: 9999,
        ),
        isFalse,
      );
    });
  });
}
