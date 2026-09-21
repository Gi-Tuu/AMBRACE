import "dart:async";

import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";

import "package:ai_companion/services/channel_status.dart";

/// 通道状态记录器纯逻辑测试（2026-09-21 P1）：错误码分类 + 记账 + 落盘 key
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test("classifyShizukuStderr 命中四类 native 文案", () {
    expect(
      ChannelStatusTracker.classifyShizukuStderr(
          "Shizuku 服务未运行：请打开 Shizuku 应用启动服务"),
      ChannelCode.serverDown,
    );
    expect(
      ChannelStatusTracker.classifyShizukuStderr(
          "未获得 Shizuku 授权：请在 Shizuku 应用中为本应用开启授权"),
      ChannelCode.noPermission,
    );
    expect(
      ChannelStatusTracker.classifyShizukuStderr("Shizuku 权限不足：请开启授权"),
      ChannelCode.noPermission,
    );
    expect(
      ChannelStatusTracker.classifyShizukuStderr(
          "Shizuku 连接异常：请先重启 Shizuku 服务"),
      ChannelCode.deadObject,
    );
    expect(
      ChannelStatusTracker.classifyShizukuStderr("DeadObject / process hasn't exited"),
      ChannelCode.deadObject,
    );
    expect(
      ChannelStatusTracker.classifyShizukuStderr(
          "命令执行超时（8000ms），Shizuku 服务可能异常"),
      ChannelCode.timeout,
    );
    expect(ChannelStatusTracker.classifyShizukuStderr("其他未知报错"), ChannelCode.unknown);
  });

  test("classifyException 命中超时与网络异常", () {
    expect(
      ChannelStatusTracker.classifyException(TimeoutException("slow")),
      ChannelCode.timeout,
    );
    expect(
      ChannelStatusTracker.classifyException(Exception("Connection refused")),
      ChannelCode.networkError,
    );
    expect(
      ChannelStatusTracker.classifyException(Exception("SocketException: closed")),
      ChannelCode.networkError,
    );
    expect(
      ChannelStatusTracker.classifyException(Exception("Failed host lookup: x")),
      ChannelCode.networkError,
    );
    expect(ChannelStatusTracker.classifyException(StateError("boom")), ChannelCode.unknown);
  });

  test("连续失败计数累加且保留 lastOkAt；成功一次后归零", () async {
    const ch = "t_core";
    final okAt = DateTime.now();
    await ChannelStatusTracker.recordOk(ch);
    final afterOk = ChannelStatusTracker.of(ch);
    expect(afterOk, isNotNull);
    expect(afterOk!.code, ChannelCode.ok);
    expect(afterOk.failCount, 0);
    expect(afterOk.lastOkAt, isNotNull);

    for (var i = 0; i < 3; i++) {
      await ChannelStatusTracker.recordFail(ch, ChannelCode.timeout, detail: "t$i");
    }
    final afterFails = ChannelStatusTracker.of(ch);
    expect(afterFails!.failCount, 3);
    expect(afterFails.code, ChannelCode.timeout);
    expect(afterFails.detail, "t2");
    expect(afterFails.lastErrorAt, isNotNull);
    // lastOkAt 保留（不被失败清空）
    expect(afterFails.lastOkAt, isNotNull);
    expect(afterFails.lastOkAt!.isAtSameMomentAs(okAt) ||
        afterFails.lastOkAt!.isAfter(okAt), isTrue);

    await ChannelStatusTracker.recordOk(ch);
    final recovered = ChannelStatusTracker.of(ch);
    expect(recovered!.failCount, 0);
    expect(recovered.code, ChannelCode.ok);
    expect(recovered.detail, "");
  });

  test("落盘 key 前缀为 chan_status_，snapshotAll 可读回", () async {
    const ch = "t_snap";
    await ChannelStatusTracker.recordFail(ch, ChannelCode.networkError,
        detail: "boom", retriable: true);

    final prefs = await SharedPreferences.getInstance();
    final keys = prefs.getKeys().where((k) => k.startsWith("chan_status_")).toList();
    expect(keys, contains("chan_status_$ch"));
    expect(prefs.getString("chan_status_$ch"), isNotNull);

    final snap = await ChannelStatusTracker.snapshotAll();
    expect(snap.containsKey(ch), isTrue);
    final m = snap[ch] as Map<String, dynamic>;
    expect(m["code"], "networkError");
    expect(m["retriable"], isTrue);
    expect(m["failCount"], 1);
    expect(m["detail"], "boom");
  });
}
