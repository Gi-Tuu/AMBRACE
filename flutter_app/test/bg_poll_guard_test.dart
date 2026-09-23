import "package:flutter_test/flutter_test.dart";

import "package:ai_companion/services/bg_poll_guard.dart";

/// 后台会话骨架守卫测试（2026-09-22 P6：S5 轮询重入保护 + S7 长连接跟随；
/// 2026-09-22 P8：补 S6 退避抖动 + 心跳探活判定）
///
/// 纯逻辑，无插件依赖：不初始化 binding、不碰 SharedPreferences / Keystore，
/// 只覆盖 `bg_poll_guard.dart` 的判定函数与抖动器。
void main() {
  group("shouldSkipPoll（S5）", () {
    test("在途（上一轮未结束）→ 跳过本轮", () {
      expect(shouldSkipPoll(inFlight: true), isTrue);
    });

    test("空闲（上一轮已结束）→ 正常跑本轮", () {
      expect(shouldSkipPoll(inFlight: false), isFalse);
    });
  });

  group("needWsRestart（S7）", () {
    test("① 未启动 + 地址/token 齐全 → 该启动", () {
      expect(
        needWsRestart(
          curServerUrl: "http://10.0.0.2:8000",
          curToken: "tok-a",
          wsServerUrl: "",
          wsToken: "",
          wsActive: false,
        ),
        isTrue,
      );
    });

    test("② 已启动且地址/token 都没变 → 不重建", () {
      expect(
        needWsRestart(
          curServerUrl: "http://10.0.0.2:8000",
          curToken: "tok-a",
          wsServerUrl: "http://10.0.0.2:8000",
          wsToken: "tok-a",
          wsActive: true,
        ),
        isFalse,
      );
    });

    test("③ 已启动但 token 变了（换账号）→ 重建", () {
      expect(
        needWsRestart(
          curServerUrl: "http://10.0.0.2:8000",
          curToken: "tok-b",
          wsServerUrl: "http://10.0.0.2:8000",
          wsToken: "tok-a",
          wsActive: true,
        ),
        isTrue,
      );
    });

    test("④ 已启动但地址变了（换服务器）→ 重建", () {
      expect(
        needWsRestart(
          curServerUrl: "http://192.168.1.9:8000",
          curToken: "tok-a",
          wsServerUrl: "http://10.0.0.2:8000",
          wsToken: "tok-a",
          wsActive: true,
        ),
        isTrue,
      );
    });

    test("⑤ 未启动且地址或 token 为空 → 维持不启动", () {
      expect(
        needWsRestart(
          curServerUrl: "",
          curToken: "tok-a",
          wsServerUrl: "",
          wsToken: "",
          wsActive: false,
        ),
        isFalse,
      );
      expect(
        needWsRestart(
          curServerUrl: "http://10.0.0.2:8000",
          curToken: "",
          wsServerUrl: "",
          wsToken: "",
          wsActive: false,
        ),
        isFalse,
      );
      expect(
        needWsRestart(
          curServerUrl: "",
          curToken: "",
          wsServerUrl: "",
          wsToken: "",
          wsActive: false,
        ),
        isFalse,
      );
    });

    test("⑥ 已启动但 token 变空（登出）→ 重建（调用方会 dispose 且不再新建）", () {
      expect(
        needWsRestart(
          curServerUrl: "http://10.0.0.2:8000",
          curToken: "",
          wsServerUrl: "http://10.0.0.2:8000",
          wsToken: "tok-a",
          wsActive: true,
        ),
        isTrue,
      );
    });
  });

  group("jitterBackoff（S6 退避抖动）", () {
    test("unit=0 / unit=1 恰为 -20% / +20% 边界", () {
      expect(
        jitterBackoff(const Duration(seconds: 10), 0),
        const Duration(milliseconds: 8000),
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), 1),
        const Duration(milliseconds: 12000),
      );
    });

    test("任意随机值都落在 [0.8×base, 1.2×base] 内", () {
      const base = Duration(seconds: 10);
      const lo = Duration(milliseconds: 8000);
      const hi = Duration(milliseconds: 12000);
      for (var i = 0; i <= 100; i++) {
        final d = jitterBackoff(base, i / 100);
        expect(d >= lo, isTrue, reason: "unit=${i / 100} → $d 低于下限");
        expect(d <= hi, isTrue, reason: "unit=${i / 100} → $d 高于上限");
      }
    });

    test("越界随机值钳到边界，不会越界也不会为负", () {
      expect(
        jitterBackoff(const Duration(seconds: 10), -1),
        const Duration(milliseconds: 8000),
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), 2),
        const Duration(milliseconds: 12000),
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), double.nan),
        const Duration(milliseconds: 8000),
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), double.infinity),
        const Duration(milliseconds: 12000),
      );
      // 基数为 0 时抖动后仍是 0（不会变成负 Duration）
      expect(jitterBackoff(Duration.zero, 0.5), Duration.zero);
      // 最小的 1s 退避：下限 800ms
      expect(
        jitterBackoff(const Duration(seconds: 1), 0),
        const Duration(milliseconds: 800),
      );
    });

    test("ratio=0 原样返回；ratio 越界按 1 处理", () {
      expect(
        jitterBackoff(const Duration(seconds: 10), 0.5, ratio: 0),
        const Duration(seconds: 10),
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), 0, ratio: 5),
        Duration.zero,
      );
      expect(
        jitterBackoff(const Duration(seconds: 10), 1, ratio: 5),
        const Duration(seconds: 20),
      );
    });
  });

  group("BackoffJitter（S6 随机源可注入）", () {
    test("注入固定随机源 → 结果确定且可复现", () {
      final jitter = BackoffJitter(random: () => 0.0);
      expect(
        jitter.apply(const Duration(seconds: 10)),
        const Duration(milliseconds: 8000),
      );
      expect(
        jitter.apply(const Duration(seconds: 10)),
        const Duration(milliseconds: 8000),
      );
    });

    test("注入序列随机源 → 逐个值落在 ±20% 区间", () {
      const seq = [0.0, 0.25, 0.5, 0.75, 1.0];
      var i = 0;
      final jitter = BackoffJitter(random: () => seq[i++]);
      expect(jitter.apply(const Duration(seconds: 10)), const Duration(milliseconds: 8000));
      expect(jitter.apply(const Duration(seconds: 10)), const Duration(milliseconds: 9000));
      expect(jitter.apply(const Duration(seconds: 10)), const Duration(milliseconds: 10000));
      expect(jitter.apply(const Duration(seconds: 10)), const Duration(milliseconds: 11000));
      expect(jitter.apply(const Duration(seconds: 10)), const Duration(milliseconds: 12000));
      expect(i, seq.length, reason: "随机源调用次数应与 apply 次数一致");
    });

    test("none → 不抖动、不消费随机源", () {
      var calls = 0;
      final jitter = BackoffJitter(ratio: 0, random: () {
        calls++;
        return 0.0;
      });
      expect(jitter.apply(const Duration(seconds: 7)), const Duration(seconds: 7));
      expect(calls, 0);
      expect(
        BackoffJitter.none.apply(const Duration(seconds: 7)),
        const Duration(seconds: 7),
      );
    });

    test("ratio 非法（NaN）时不放大也不缩水", () {
      expect(
        jitterBackoff(const Duration(seconds: 10), 0.0, ratio: double.nan),
        const Duration(seconds: 10),
      );
    });
  });

  group("心跳探活判定（S6）", () {
    test("本周期收到过数据（含 pong）→ 不发 ping", () {
      expect(shouldPingOnHeartbeatTick(dataSinceLastTick: 1), isFalse);
      expect(shouldPingOnHeartbeatTick(dataSinceLastTick: 9), isFalse);
    });

    test("本周期零数据 → 发 ping 探活", () {
      expect(shouldPingOnHeartbeatTick(dataSinceLastTick: 0), isTrue);
    });

    test("连续 2 轮无数据 → 判半开；1 轮不算", () {
      expect(isHeartbeatHalfOpen(missCount: 0), isFalse);
      expect(isHeartbeatHalfOpen(missCount: 1), isFalse);
      expect(isHeartbeatHalfOpen(missCount: 2), isTrue);
      expect(isHeartbeatHalfOpen(missCount: 9), isTrue);
    });

    test("阈值可覆盖，missLimit<=0 表示永不判半开", () {
      expect(isHeartbeatHalfOpen(missCount: 5, missLimit: 3), isTrue);
      expect(isHeartbeatHalfOpen(missCount: 5, missLimit: 0), isFalse);
    });

    test("默认口径：30s 一轮 × 2 轮 = 60s；抖动 ±20%", () {
      expect(kHeartbeatInterval, const Duration(seconds: 30));
      expect(kHeartbeatMissLimit, 2);
      expect(kHeartbeatInterval * kHeartbeatMissLimit, const Duration(seconds: 60));
      expect(kBackoffJitterRatio, 0.2);
    });
  });
}
