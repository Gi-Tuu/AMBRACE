import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";

import "package:ai_companion/services/channel_status.dart";
import "package:ai_companion/services/shizuku_service.dart";

/// Shizuku 重试 / 自愈策略测试（2026-09-21 P3）
///
/// Kotlin 侧的退避重试（500ms → 1500ms、每次重试前 pingBinder 探活、超时不重试）
/// 无法用 Dart 单测覆盖，那部分由代码走查 + 编译验证兜底（见派单 §4）。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test("codeFromNative：五个取值各命中一个枚举", () {
    expect(
      ChannelStatusTracker.codeFromNative("serverDown"),
      ChannelCode.serverDown,
    );
    expect(
      ChannelStatusTracker.codeFromNative("noPermission"),
      ChannelCode.noPermission,
    );
    expect(
      ChannelStatusTracker.codeFromNative("deadObject"),
      ChannelCode.deadObject,
    );
    expect(ChannelStatusTracker.codeFromNative("timeout"), ChannelCode.timeout);
    expect(ChannelStatusTracker.codeFromNative("ok"), ChannelCode.ok);
  });

  test("codeFromNative：未知 / 空 → unknown", () {
    expect(ChannelStatusTracker.codeFromNative("whoKnows"), ChannelCode.unknown);
    expect(ChannelStatusTracker.codeFromNative(""), ChannelCode.unknown);
    expect(ChannelStatusTracker.codeFromNative(null), ChannelCode.unknown);
  });

  test("shouldOfferUserAction：只有 serverDown / noPermission 需要用户动作", () {
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.serverDown), isTrue);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.noPermission), isTrue);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.deadObject), isFalse);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.timeout), isFalse);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.networkError), isFalse);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.unknown), isFalse);
  });

  test("recordFail(retriable: true) 落盘后 of() / snapshotAll() 都能读回", () async {
    const ch = "t_retry_policy";
    await ChannelStatusTracker.recordFail(ch, ChannelCode.deadObject,
        detail: "Shizuku 连接异常：请先重启 Shizuku 服务，再回到本页重试",
        retriable: true);

    final st = ChannelStatusTracker.of(ch);
    expect(st, isNotNull);
    expect(st!.retriable, isTrue);
    expect(st.code, ChannelCode.deadObject);
    expect(st.failCount, 1);

    final snap = await ChannelStatusTracker.snapshotAll();
    expect(snap.containsKey(ch), isTrue);
    final m = snap[ch] as Map<String, dynamic>;
    expect(m["retriable"], isTrue);
    expect(m["code"], "deadObject");
  });

  test("前置检查失败（serverDown / noPermission）不计为可重试", () async {
    const chDown = "t_user_action_down";
    const chPerm = "t_user_action_perm";
    await ChannelStatusTracker.recordFail(chDown, ChannelCode.serverDown,
        detail: "Shizuku 服务未运行", retriable: false);
    await ChannelStatusTracker.recordFail(chPerm, ChannelCode.noPermission,
        detail: "未获得 Shizuku 授权", retriable: false);

    expect(ChannelStatusTracker.of(chDown)!.retriable, isFalse);
    expect(ChannelStatusTracker.of(chPerm)!.retriable, isFalse);
    // 但两者都需要用户动作
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.serverDown), isTrue);
    expect(ShizukuService.shouldOfferUserAction(ChannelCode.noPermission), isTrue);
  });
}
