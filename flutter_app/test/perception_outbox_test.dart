import "dart:convert";
import "dart:io";

import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";

import "package:ai_companion/services/api_client.dart";
import "package:ai_companion/services/channel_status.dart";
import "package:ai_companion/services/perception_outbox.dart";

import "fake_api_adapter.dart";

/// 感知上传本地队列纯逻辑测试（2026-09-21 P2，治盘点 S3；2026-09-22 P2b：存储改「一条一文件」）。
/// path_provider 在 flutter test 下无平台实现，用 overrideRootForTest 注入临时目录
/// （生产不注入，仍走 getApplicationSupportDirectory）；
/// 时间用 overrideNowForTest 注入（**不改系统时间**）。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory root;

  const server = "http://127.0.0.1:8000";

  void mockAccount(int uid, {String url = server}) {
    SharedPreferences.setMockInitialValues({
      "user_id": uid,
      "server_url": url,
    });
  }

  setUp(() async {
    root = await Directory.systemTemp.createTemp("ambrace_outbox_");
    PerceptionOutbox.overrideRootForTest(root);
    mockAccount(1);
  });

  tearDown(() async {
    PerceptionOutbox.overrideRootForTest(null);
    PerceptionOutbox.overrideNowForTest(DateTime.now);
    if (root.existsSync()) await root.delete(recursive: true);
  });

  /// 账号队列目录（与实现同口径：服务器地址非字母数字 → `_`）
  Directory dirOf(int uid) =>
      Directory("${root.path}/perception_outbox/${_safeServer(server)}/$uid");

  /// 队列目录里的记录文件（`*.json`；一条一文件）
  List<File> queueFiles(int uid) {
    final dir = dirOf(uid);
    if (!dir.existsSync()) return [];
    return dir
        .listSync()
        .whereType<File>()
        .where((f) => f.path.endsWith(".json"))
        .toList();
  }

  /// 读队列内容（按入队顺序，校验「丢最旧」的取舍顺序而不只是条数）
  List<Map<String, dynamic>> dump(int uid) {
    final items = queueFiles(uid)
        .map(
          (f) => Map<String, dynamic>.from(jsonDecode(f.readAsStringSync()) as Map),
        )
        .toList();
    items.sort((a, b) {
      final ta = (a["createdAtMs"] as num?)?.toInt() ?? 0;
      final tb = (b["createdAtMs"] as num?)?.toInt() ?? 0;
      if (ta != tb) return ta.compareTo(tb);
      return ((a["seq"] as num?)?.toInt() ?? 0)
          .compareTo((b["seq"] as num?)?.toInt() ?? 0);
    });
    return items;
  }

  test("入队与计数：空内容不入队、同 clientKey 不重复入队", () async {
    expect(await PerceptionOutbox.pendingCount(), 0);

    await PerceptionOutbox.enqueue(source: "clipboard", content: "你好世界");
    expect(await PerceptionOutbox.pendingCount(), 1);

    // 同一次采集重复入队 → 同一个 key，不产生第二条
    await PerceptionOutbox.enqueue(source: "clipboard", content: "你好世界");
    expect(await PerceptionOutbox.pendingCount(), 1);

    // 空/全空白内容不入队
    await PerceptionOutbox.enqueue(source: "clipboard", content: "   \n ");
    expect(await PerceptionOutbox.pendingCount(), 1);

    // 不同 source 视为不同条目
    await PerceptionOutbox.enqueue(source: "notification", content: "你好世界");
    expect(await PerceptionOutbox.pendingCount(), 2);

    // clientKey 规则：source|长度|hashCode 绝对值（确定性，无需新增依赖）
    final key = PerceptionOutbox.clientKeyOf("clipboard", "你好世界");
    expect(key.startsWith("clipboard|4|"), isTrue);
    expect(key, PerceptionOutbox.clientKeyOf("clipboard", "你好世界"));
    expect(
      dump(1).map((e) => e["clientKey"]).toSet(),
      containsAll([key, PerceptionOutbox.clientKeyOf("notification", "你好世界")]),
    );
  });

  test("markSent 后队列长度减 1", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "第一条");
    await PerceptionOutbox.enqueue(source: "clipboard", content: "第二条");
    expect(await PerceptionOutbox.pendingCount(), 2);

    await PerceptionOutbox.markSent(PerceptionOutbox.clientKeyOf("clipboard", "第一条"));
    expect(await PerceptionOutbox.pendingCount(), 1);
    expect(dump(1).single["content"], "第二条");

    // 未知 key 不误删
    await PerceptionOutbox.markSent("clipboard|99|not-exist");
    expect(await PerceptionOutbox.pendingCount(), 1);
  });

  test("上限 200：入 205 条后最旧的 5 条被丢，并记一笔通道状态", () async {
    for (var i = 1; i <= 205; i++) {
      await PerceptionOutbox.enqueue(source: "clipboard", content: "item-$i");
    }
    expect(await PerceptionOutbox.pendingCount(), PerceptionOutbox.maxEntries);

    final contents = dump(1).map((e) => e["content"]).toList();
    expect(contents.first, "item-6"); // 最旧 5 条已丢
    expect(contents.last, "item-205");
    expect(contents, isNot(contains("item-1")));

    final state = (await ChannelStatusTracker
            .snapshotAll())["perception_outbox_dropped"];
    expect(state, isNotNull, reason: "丢弃必须被观测到");
    expect((state as Map)["detail"], matches(RegExp(r"dropped=\d+")));
    expect(state["failCount"], greaterThan(0));
  });

  test("账号隔离：user_id 之间互不可见；取不到 user_id 不入队", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "账号 1 的剪贴板");
    expect(await PerceptionOutbox.pendingCount(), 1);

    mockAccount(2);
    expect(await PerceptionOutbox.pendingCount(), 0);
    await PerceptionOutbox.enqueue(source: "clipboard", content: "账号 2 的剪贴板");
    expect(await PerceptionOutbox.pendingCount(), 1);

    // 回到账号 1：仍只看到自己的那条，内容没串
    mockAccount(1);
    expect(await PerceptionOutbox.pendingCount(), 1);
    expect(dump(1).single["content"], "账号 1 的剪贴板");
    expect(dump(2).single["content"], "账号 2 的剪贴板");

    // 未登录（0 / 缺 key）：宁可丢也不跨账号串数据 → 不入队、不计数、不建目录
    mockAccount(0);
    await PerceptionOutbox.enqueue(source: "clipboard", content: "匿名数据");
    expect(await PerceptionOutbox.pendingCount(), 0);
    expect(dirOf(0).existsSync(), isFalse);

    SharedPreferences.setMockInitialValues({"server_url": server});
    expect(await PerceptionOutbox.pendingCount(), 0);
    await PerceptionOutbox.enqueue(source: "clipboard", content: "匿名数据");
    expect(await PerceptionOutbox.pendingCount(), 0);
    expect(dirOf(0).existsSync(), isFalse);
  });

  test("clear 一次清空本账号队列；flush 空队列不发起请求", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "待清除");
    expect(await PerceptionOutbox.pendingCount(), 1);
    await PerceptionOutbox.clear();
    expect(await PerceptionOutbox.pendingCount(), 0);
    expect(await PerceptionOutbox.flush(), 0);
  });

  test("flush 补传：成功即清队列并返回条数", () async {
    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"status": "ok"});
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "clipboard", content: "断网期间的剪贴板");
    await PerceptionOutbox.enqueue(source: "notification", content: "断网期间的通知");
    expect(await PerceptionOutbox.pendingCount(), 2);

    expect(await PerceptionOutbox.flush(), 2);
    expect(await PerceptionOutbox.pendingCount(), 0);
    expect(adapter.requests, hasLength(2));
    expect(
      adapter.requests.map((r) => r.path).toSet(),
      {"/api/v1/phone/perception"},
    );
  });

  test("flush 单条失败即停止本轮（保持顺序，不跳过继续打）", () async {
    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"detail": "boom"}, status: 404);
    ApiClient().dio.httpClientAdapter = adapter;

    for (final c in ["第一条", "第二条", "第三条"]) {
      await PerceptionOutbox.enqueue(source: "clipboard", content: c);
    }
    expect(await PerceptionOutbox.flush(), 0);
    expect(adapter.requests, hasLength(1), reason: "第一条失败就该停，不再打后面两条");
    expect(await PerceptionOutbox.pendingCount(), 3, reason: "数据仍在队列里等下轮");
    // 顺序不变：最旧的一条仍排在队首
    expect(dump(1).first["content"], "第一条");
  });

  test("flush 每轮最多 20 条", () async {
    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"status": "ok"});
    ApiClient().dio.httpClientAdapter = adapter;

    for (var i = 1; i <= 25; i++) {
      await PerceptionOutbox.enqueue(source: "clipboard", content: "item-$i");
    }
    expect(await PerceptionOutbox.flush(), PerceptionOutbox.flushBatch);
    expect(adapter.requests, hasLength(PerceptionOutbox.flushBatch));
    expect(await PerceptionOutbox.pendingCount(), 5);
    // 剩下的仍是最新的 5 条（最旧 20 条已补传成功）
    expect(dump(1).first["content"], "item-21");
  });

  // === P2b 新增覆盖 ===

  test("TTL 3 天：过期条目在入队裁剪时被剔掉并记一笔丢弃", () async {
    final t0 = DateTime(2026, 9, 1, 10);
    PerceptionOutbox.overrideNowForTest(() => t0);
    await PerceptionOutbox.enqueue(source: "clipboard", content: "旧数据一");
    await PerceptionOutbox.enqueue(source: "clipboard", content: "旧数据二");
    expect(await PerceptionOutbox.pendingCount(), 2);

    // 时间跳到 T0 + 4 天（不改系统时间，只换注入的 now）：前两条已过 TTL
    PerceptionOutbox.overrideNowForTest(() => t0.add(const Duration(days: 4)));
    await PerceptionOutbox.enqueue(source: "clipboard", content: "新数据");

    expect(await PerceptionOutbox.pendingCount(), 1);
    expect(dump(1).single["content"], "新数据");

    final state = (await ChannelStatusTracker
            .snapshotAll())["perception_outbox_dropped"];
    expect(state, isNotNull, reason: "过期丢弃必须被观测到");
    expect((state as Map)["detail"], matches(RegExp(r"dropped=\d+")));
    expect(state["failCount"], greaterThan(0));
  });

  test("flush 记账：全成记一次 ok", () async {
    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"status": "ok"});
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "clipboard", content: "补传成功的一条");
    expect(await PerceptionOutbox.flush(), 1);

    final state = (await ChannelStatusTracker
            .snapshotAll())[ChannelStatusTracker.kPerceptionUpload];
    expect(state, isNotNull, reason: "flush 成功必须被观测到");
    expect((state as Map)["code"], "ok");
  });

  test("flush 记账：有失败记一次 retriable", () async {
    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"detail": "boom"}, status: 404);
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "clipboard", content: "补传失败的一条");
    expect(await PerceptionOutbox.flush(), 0);

    final state = (await ChannelStatusTracker
            .snapshotAll())[ChannelStatusTracker.kPerceptionUpload];
    expect(state, isNotNull, reason: "flush 失败必须被观测到");
    expect((state as Map)["retriable"], isTrue);
    expect(state["failCount"], greaterThanOrEqualTo(1));
    expect(state["code"], "networkError");
  });

  test("残留 .tmp 不算待发、也不会被 flush 发送", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "正常一条");
    final tmp = File("${dirOf(1).path}/residual.tmp");
    await tmp.writeAsString(
      jsonEncode({
        "source": "clipboard",
        "content": "半截写入的残留",
        "clientKey": "clipboard|6|residual",
        "createdAtMs": DateTime.now().millisecondsSinceEpoch,
        "attempts": 0,
      }),
    );

    expect(await PerceptionOutbox.pendingCount(), 1, reason: ".tmp 残留不计入待发");

    final adapter = FakeApiAdapter()
      ..json("POST", PerceptionOutbox.endpoint, {"status": "ok"});
    ApiClient().dio.httpClientAdapter = adapter;
    expect(await PerceptionOutbox.flush(), 1);
    expect(adapter.requests, hasLength(1), reason: ".tmp 不该被发送");
    expect(tmp.existsSync(), isTrue);
  });

  test("坏文件被清理：pendingCount 不抛异常且文件被删", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "正常一条");
    final bad = File("${dirOf(1).path}/bad.json");
    await bad.writeAsString("{不是 json");

    expect(await PerceptionOutbox.pendingCount(), 1);
    expect(bad.existsSync(), isFalse, reason: "坏文件要清掉，不留垃圾");
  });

  test("一条一文件：每条一个 *.json，markSent 只删对应那条", () async {
    await PerceptionOutbox.enqueue(source: "clipboard", content: "第一条");
    await PerceptionOutbox.enqueue(source: "notification", content: "第二条");
    expect(queueFiles(1), hasLength(2));
    expect(dirOf(1).existsSync(), isTrue);

    await PerceptionOutbox.markSent(
      PerceptionOutbox.clientKeyOf("clipboard", "第一条"),
    );
    expect(queueFiles(1), hasLength(1));
    expect(dump(1).single["content"], "第二条");
  });
}

/// 服务器地址安全化（与实现同口径：非字母数字 → `_`）
String _safeServer(String url) => url.replaceAll(RegExp("[^A-Za-z0-9]"), "_");
