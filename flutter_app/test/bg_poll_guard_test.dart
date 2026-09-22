import "package:flutter_test/flutter_test.dart";

import "package:ai_companion/services/bg_poll_guard.dart";

/// 后台会话骨架守卫测试（2026-09-22 P6：S5 轮询重入保护 + S7 长连接跟随）
///
/// 纯逻辑，无插件依赖：不初始化 binding、不碰 SharedPreferences / Keystore，
/// 只覆盖 `bg_poll_guard.dart` 的两个判定函数。
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
}
