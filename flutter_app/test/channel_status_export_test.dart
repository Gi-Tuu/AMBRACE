import "dart:io";

import "package:dio/dio.dart";
import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";

import "package:ai_companion/services/api_client.dart";
import "package:ai_companion/services/channel_status.dart";
import "package:ai_companion/services/perception_outbox.dart";
import "package:ai_companion/services/phone_perception_service.dart";

import "fake_api_adapter.dart";

/// P4（2026-09-22）：诊断文本（感知日志 + 通道状态）固定格式，
/// 以及一键清除返回「本地已清 / 服务端没清」的结构化结果。
/// 网络一律走 [FakeApiAdapter]，不打真实请求。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const clearEndpoint = "/api/v1/phone/perception";
  late Directory root;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      "user_id": 1,
      "server_url": "http://127.0.0.1:8000",
    });
    root = await Directory.systemTemp.createTemp("ambrace_p4_");
    PerceptionOutbox.overrideRootForTest(root);
  });

  tearDown(() async {
    PerceptionOutbox.overrideRootForTest(null);
    if (root.existsSync()) await root.delete(recursive: true);
  });

  /// 统一按 \n 切行（StringBuffer.writeln 用 \n，此处只做防御）
  List<String> linesOf(String text) =>
      text.replaceAll("\r\n", "\n").trimRight().split("\n");

  test("formatDiagnostics：通道按名排序、空值写 -、时间本地可读", () {
    final text = PhonePerceptionService.formatDiagnostics(
      channels: {
        "shizuku_snapshot": {
          "code": "deadObject",
          "retriable": true,
          "lastOkAt": null,
          "lastErrorAt": "2026-09-22T13:54:06.000",
          "failCount": 3,
          "detail": "Shizuku 连接异常：process hasn't exited",
        },
        "accessibility": {
          "code": "ok",
          "retriable": false,
          "lastOkAt": "2026-09-22T09:08:07.000",
          "lastErrorAt": null,
          "failCount": 0,
          "detail": "",
        },
      },
      logContent: "13:54:06 snapshot ok\n13:55:01 upload failed",
      logPath: "/storage/emulated/0/Android/data/x/files/logs/phone_perception.log",
    );
    final lines = linesOf(text);

    expect(lines.first, "=== perception log ===");
    expect(lines[1], "path: /storage/emulated/0/Android/data/x/files/logs/phone_perception.log");
    // 日志原文整段带上（两行都在，且没被 unavailable 顶掉）
    expect(lines.contains("13:54:06 snapshot ok"), isTrue);
    expect(lines.contains("13:55:01 upload failed"), isTrue);
    expect(text, isNot(contains("log: unavailable")));

    final a = lines.firstWhere((l) => l.startsWith("accessibility "));
    final s = lines.firstWhere((l) => l.startsWith("shizuku_snapshot "));
    expect(
      a,
      "accessibility  code=ok retriable=false fails=0"
      " lastOk=2026-09-22 09:08:07 lastErr=- detail=-",
    );
    expect(
      s,
      "shizuku_snapshot  code=deadObject retriable=true fails=3"
      " lastOk=- lastErr=2026-09-22 13:54:06"
      " detail=Shizuku 连接异常：process hasn't exited",
    );
    // 排序：accessibility 在 shizuku_snapshot 前（与传入顺序无关，可复现）
    expect(lines.indexOf(a), lessThan(lines.indexOf(s)));
    // 一条通道一行：detail 里不能夹换行
    expect(s.split("\n"), hasLength(1));
    expect(
      lines.where((l) => RegExp(r"^\S+  code=").hasMatch(l)),
      hasLength(2),
    );
  });

  test("formatDiagnostics：detail 含换行时折成一行；无通道时给出可读占位", () {
    final text = PhonePerceptionService.formatDiagnostics(
      channels: {
        "notification": {
          "code": "networkError",
          "retriable": false,
          "lastOkAt": null,
          "lastErrorAt": "2026-09-22T00:00:01.000",
          "failCount": 1,
          "detail": "行一\n行二",
        },
      },
      logContent: "x",
    );
    final line = linesOf(text).last;
    expect(line, endsWith("detail=行一 行二"));
    expect(
      line,
      "notification  code=networkError retriable=false fails=1"
      " lastOk=- lastErr=2026-09-22 00:00:01 detail=行一 行二",
    );

    final empty = linesOf(
      PhonePerceptionService.formatDiagnostics(channels: {}, logContent: "x"),
    );
    expect(empty.last, "(none)");
  });

  test("formatDiagnostics：日志拿不到 → log: unavailable(<原因>)，不抛异常", () {
    final failed = PhonePerceptionService.formatDiagnostics(
      channels: const {},
      logContent: "",
      logError: "not android",
    );
    expect(failed, contains("log: unavailable(not android)"));
    expect(failed, contains("path: -"));

    // 空内容且没带原因：仍要有一行 unavailable，而不是悄悄留空
    final blank = PhonePerceptionService.formatDiagnostics(
      channels: const {},
      logContent: "   \n  ",
    );
    expect(linesOf(blank)[1], "path: -");
    expect(linesOf(blank)[2], "log: unavailable(empty)");
  });

  test("buildDiagnosticsText：两段都在（非 Android 测试环境走 unavailable 分支）", () async {
    // 记一笔真实失败，验证「日志段 + 通道段」两段拼接真的从 tracker 取到了数据
    await ChannelStatusTracker.recordFail(
      ChannelStatusTracker.kServiceHealth,
      ChannelCode.unknown,
      detail: "seed for p4",
    );
    final text = await PhonePerceptionService.buildDiagnosticsText();
    final lines = linesOf(text);
    expect(lines.first, "=== perception log ===");
    expect(
      lines.any((l) => l.startsWith("=== channel status ===")),
      isTrue,
      reason: "通道状态段必须跟着日志段一起出",
    );
    // flutter test 跑在桌面：native 通道不可用 → 明确写成 unavailable，而不是空文本
    expect(text, contains("log: unavailable("));
    expect(
      lines.any(
        (l) => l.startsWith("service_health  code=unknown") && l.contains("detail=seed for p4"),
      ),
      isTrue,
      reason: "记账过的通道要出现在快照里",
    );
  });

  test("clearAll：本地清成功 + 服务端成功 → serverOk=true 且队列真的空掉", () async {
    final adapter = FakeApiAdapter()
      ..json("DELETE", clearEndpoint, {"status": "ok"});
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "clipboard", content: "断网期间的剪贴板");
    expect(await PerceptionOutbox.pendingCount(), 1);

    final r = await PhonePerceptionService.clearAll();
    expect(r, {"localCleared": true, "serverOk": true, "serverError": ""});
    expect(await PerceptionOutbox.pendingCount(), 0);
    expect(adapter.requests, contains((method: "DELETE", path: clearEndpoint)));
  });

  test("clearAll：本地清成功 + 服务端报错 → serverOk=false，本地仍清空", () async {
    final adapter = FakeApiAdapter()
      ..json("DELETE", clearEndpoint, {"detail": "boom"}, status: 404);
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "clipboard", content: "服务端没删掉的一条");
    final r = await PhonePerceptionService.clearAll();

    expect(r["localCleared"], isTrue);
    expect(r["serverOk"], isFalse);
    expect(r["serverError"], isNotEmpty, reason: "要把原因留着，UI/日志才说得清为什么没清");
    expect(
      await PerceptionOutbox.pendingCount(),
      0,
      reason: "「先清本地再删服务端」的顺序不能因服务端失败而回退",
    );
    expect(await PerceptionOutbox.flush(), 0, reason: "本地已清，不该再有补传残留");
  });

  test("clearAll：断网（连接异常）→ 结构化返回 false，不把异常抛到 UI", () async {
    final adapter = FakeApiAdapter()
      ..handle(
        "DELETE",
        clearEndpoint,
        (o) => throw DioException.connectionError(
          requestOptions: o,
          reason: "offline",
        ),
      );
    ApiClient().dio.httpClientAdapter = adapter;

    await PerceptionOutbox.enqueue(source: "notification", content: "离线留着的一条");
    final r = await PhonePerceptionService.clearAll();

    expect(r["localCleared"], isTrue);
    expect(r["serverOk"], isFalse);
    expect(await PerceptionOutbox.pendingCount(), 0);
  });

  // ── P2b（2026-09-24）：诊断文本的「上下文预算」节（P2a 的读数端）──────────────
  // 口径：不传 budget 时输出必须与本节加入前逐字节一致；传了（或带 budgetError）才追加。

  /// 字段名与后端 GET /api/v1/system/context-budget 一一对应（客户端只回显、不换算）
  Map<String, dynamic> p2bBudget({Map<String, dynamic>? lastClip}) => <String, dynamic>{
    "status": "ok",
    "total_quota_tokens": 9000,
    "reserve_reply_tokens": 800,
    "reserve_tools_tokens": 500,
    "floor_tokens": 256,
    "effective_budget_tokens": 7700,
    "flag_enabled": true,
    "clip_count_24h": 2,
    "last_clip": lastClip,
    "error": "",
  };

  test("P2b：不传 budget → 输出与本节加入前逐字节一致（含空通道早返回路径）", () {
    final got = PhonePerceptionService.formatDiagnostics(
      channels: const <String, dynamic>{
        "accessibility": <String, dynamic>{
          "code": "ok",
          "retriable": false,
          "lastOkAt": null,
          "lastErrorAt": null,
          "failCount": 0,
          "detail": "",
        },
      },
      logContent: "line one\nline two",
    );
    expect(
      got,
      "=== perception log ===\n"
      "path: -\n"
      "line one\n"
      "line two\n"
      "=== channel status ===\n"
      "accessibility  code=ok retriable=false fails=0 lastOk=- lastErr=- detail=-\n",
    );
    expect(got, isNot(contains("context budget")));

    expect(
      PhonePerceptionService.formatDiagnostics(channels: const {}, logContent: "x"),
      "=== perception log ===\npath: -\nx\n=== channel status ===\n(none)\n",
    );
  });

  test("P2b：传 budget → 末尾追加预算节，逐行数值正确；detail 里的数组不外流", () {
    final text = PhonePerceptionService.formatDiagnostics(
      channels: const <String, dynamic>{},
      logContent: "x",
      budget: p2bBudget(lastClip: <String, dynamic>{
        "id": 77,
        "character_id": 13,
        "created_at": "2026-09-24 05:31:02",
        "detail": <String, dynamic>{
          "budget": 7700,
          "used": 8120,
          "reserve_reply": 800,
          "reserve_tools": 500,
          "clipped_blocks": 3,
          "freed_chars": 1024,
          "total_removed": 1024,
          "blocks": <dynamic>[
            <String, dynamic>{"removed": 10, "head": "SECRET_HEAD_ABC"},
          ],
        },
      }),
    );
    final lines = linesOf(text);
    final head = lines.indexOf("=== context budget ===");
    expect(head, greaterThan(lines.indexOf("=== channel status ===")));
    expect(head, greaterThan(0));
    // (none) 占位之后照样出预算节（旧版这里是早返回，会丢节）
    expect(lines.sublist(0, head), const [
      "=== perception log ===",
      "path: -",
      "x",
      "=== channel status ===",
      "(none)",
    ]);
    expect(lines.sublist(head), const [
      "=== context budget ===",
      "status: ok",
      "total_quota_tokens: 9000",
      "reserve_reply_tokens: 800",
      "reserve_tools_tokens: 500",
      "floor_tokens: 256",
      "effective_budget_tokens: 7700",
      "flag_enabled: true",
      "clip_count_24h: 2",
      "clip_last: 2026-09-24 05:31:02 char=13 budget=7700 clipped_blocks=3 "
          "freed_chars=1024 reserve_reply=800 reserve_tools=500 total_removed=1024 used=8120",
    ]);
    expect(text, isNot(contains("SECRET_HEAD_ABC")));
  });

  test("P2b：预算字段缺失 / 无裁剪记录 → 空值写 -、flag 未开写 false", () {
    final lines = linesOf(
      PhonePerceptionService.formatDiagnostics(
        channels: const <String, dynamic>{},
        logContent: "x",
        budget: const <String, dynamic>{"status": "ok"},
      ),
    );
    expect(lines.sublist(lines.indexOf("=== context budget ===")), const [
      "=== context budget ===",
      "status: ok",
      "total_quota_tokens: -",
      "reserve_reply_tokens: -",
      "reserve_tools_tokens: -",
      "floor_tokens: -",
      "effective_budget_tokens: -",
      "flag_enabled: false",
      "clip_count_24h: -",
      "clip_last: -",
    ]);
  });

  test("P2b：取数失败 → 只有该节 unavailable，其余段落照旧；带 budget 时补 clip_error", () {
    final lines = linesOf(
      PhonePerceptionService.formatDiagnostics(
        channels: const <String, dynamic>{},
        logContent: "line one",
        budgetError: "DioException [connection error]: offline\nhost 127.0.0.1",
      ),
    );
    expect(lines.first, "=== perception log ===");
    expect(lines, contains("line one"));
    expect(lines, contains("=== channel status ==="));
    // 原因里的换行折成一行，不能把预算节撑成两行
    expect(
      lines.last,
      "status: unavailable(DioException [connection error]: offline host 127.0.0.1)",
    );

    final both = linesOf(
      PhonePerceptionService.formatDiagnostics(
        channels: const <String, dynamic>{},
        logContent: "x",
        budget: p2bBudget(),
        budgetError: "clip_query_failed: RuntimeError('boom')",
      ),
    );
    expect(both, contains("clip_last: -"));
    expect(both.last, "clip_error: unavailable(clip_query_failed: RuntimeError('boom'))");
  });

  test("P2b：buildDiagnosticsText 透传 budget → 预算节随取数外壳一起出", () async {
    final text = await PhonePerceptionService.buildDiagnosticsText(
      budget: p2bBudget(),
    );
    expect(text, contains("=== context budget ==="));
    expect(text, contains("effective_budget_tokens: 7700"));
    expect(text, contains("=== channel status ==="));
  });
}
