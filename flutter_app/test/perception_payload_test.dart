import "dart:convert";
import "dart:io";

import "package:dio/dio.dart";
import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";

import "package:ai_companion/services/api_client.dart";
import "package:ai_companion/services/perception_outbox.dart";
import "package:ai_companion/services/phone_perception_service.dart";

import "fake_api_adapter.dart";

/// A24（X7-M1 客户端半边）：感知上报的字段级载荷 `payload_json` 与幂等键。
///
/// 服务端（`backend/app/api/phone.py`）早就收了 `payload_json`、并把「同正文不同载荷」
/// 视为两次不同采集（P9 第 3 条），但客户端一直没接线 ⇒ 库内 payload 零产出。
/// 本文件钉三件事：①载荷的编法（不新增采集、空值剔除、键序稳定、超限整份放弃）；
/// ②`clientKeyOf` **无载荷时逐字保持旧格式**（老队列文件、不带载荷的调用方都不能变键）；
/// ③载荷真的走到线上（入队 → flush 补传 / uploadSnapshot 直传，两条路径都带）。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory root;
  const server = "http://127.0.0.1:8000";

  setUp(() async {
    root = await Directory.systemTemp.createTemp("ambrace_payload_");
    PerceptionOutbox.overrideRootForTest(root);
    SharedPreferences.setMockInitialValues({
      "user_id": 1,
      "server_url": server,
    });
  });

  tearDown(() async {
    PerceptionOutbox.overrideRootForTest(null);
    PerceptionOutbox.overrideNowForTest(DateTime.now);
    if (root.existsSync()) await root.delete(recursive: true);
  });

  Directory dirOf() => Directory(
      "${root.path}/perception_outbox/${server.replaceAll(RegExp("[^A-Za-z0-9]"), "_")}/1");

  List<File> queueFiles() => !dirOf().existsSync()
      ? []
      : dirOf()
          .listSync()
          .whereType<File>()
          .where((f) => f.path.endsWith(".json"))
          .toList();

  /// 捕获发出去的表单字段（FakeApiAdapter 默认只记 method/path，载荷要看 body）
  List<Map<String, String>> captureForms(FakeApiAdapter adapter) {
    final forms = <Map<String, String>>[];
    adapter.handle("POST", PerceptionOutbox.endpoint, (o) {
      forms.add({for (final e in (o.data as FormData).fields) e.key: e.value});
      return FakeApiAdapter.body({"status": "ok"});
    });
    ApiClient().dio.httpClientAdapter = adapter;
    return forms;
  }

  group("buildPayload", () {
    test("上限常量钉死（与服务端 MAX_PAYLOAD_JSON / 渲染侧截断同口径）", () {
      expect(PhonePerceptionService.maxPayloadChars, 4000);
      expect(PhonePerceptionService.maxPayloadValueChars, 300);
      expect(PhonePerceptionService.maxPayloadItems, 8);
    });

    test("空值剔除 + 键按字母序：同一份数据必得同一个串", () {
      final a = PhonePerceptionService.buildPayload({
        "z": 1,
        "a": "文本",
        "empty": "   ",
        "n": null,
        "list": <String>[],
      });
      expect(a, '{"a":"文本","z":1}');
      // 键序抖动不影响结果（clientKey 稳定性的前提）
      expect(
        PhonePerceptionService.buildPayload({"z": 1, "a": "文本"}),
        a,
      );
    });

    test("全空 ⇒ null（＝不发该字段，请求与 A24 之前逐字一致）", () {
      expect(PhonePerceptionService.buildPayload({}), isNull);
      expect(
        PhonePerceptionService.buildPayload({"a": "", "b": null, "c": <String>[]}),
        isNull,
      );
    });

    test("单值截 300；列表截 8 条；条目内空字段剔除、空条目整条剔除", () {
      final one = jsonDecode(
        PhonePerceptionService.buildPayload({"text": "字" * 400})!,
      ) as Map;
      expect((one["text"] as String).length, 300);

      final many = jsonDecode(
        PhonePerceptionService.buildPayload({
          "count": 20,
          "items": List.generate(20, (i) => {"name": "n$i", "date": ""}),
        })!,
      ) as Map;
      expect(many["items"], hasLength(8));
      expect((many["items"] as List).first.keys.toList(), ["name"]);

      final allBlank = PhonePerceptionService.buildPayload({
        "count": 3,
        "items": [{"name": "", "date": null}],
      });
      expect(jsonDecode(allBlank!) as Map, {"count": 3}, reason: "空条目整条剔除");
    });

    test("序列化后超过 4000 ⇒ 整份载荷放弃，正文不受影响", () {
      final wide = {for (var i = 1; i <= 15; i++) "f$i": "x" * 400};
      expect(
        PhonePerceptionService.buildPayload(wide),
        isNull,
        reason: "脏数据绝不该让一次采集整体失败",
      );
      final fits = PhonePerceptionService.buildPayload({"f1": "x" * 400})!;
      expect(fits.length, lessThanOrEqualTo(4000));
    });
  });

  group("buildShizukuPayload：与 formatSnapshot 同一批字段、同一套缺值口径", () {
    test("正文没写出来的事实，载荷里也不出现", () {
      final p = jsonDecode(
        PhonePerceptionService.buildShizukuPayload({
          "screenOn": false,
          "screenOnMs": 0,
          "foregroundApp": "",
          "batteryLevel": 66,
          "batteryCharging": false,
          "network": "wifi",
          "dnd": false,
          "device": "vivo V2507A",
          "androidVersion": "16",
        })!,
      ) as Map;
      expect(
        p.keys.toSet(),
        {"android_version", "battery_percent", "dnd", "device", "network", "screen_on"},
      );
      expect(p["screen_on"], isFalse, reason: "正文写了「屏幕熄灭」，这是事实不是缺值");
      expect(p["dnd"], isFalse, reason: "正文写了「勿扰：关闭」");
      expect(p["battery_percent"], 66);
      expect(p.containsKey("battery_charging"), isFalse, reason: "非充电时正文不写");
      expect(p.containsKey("screen_on_minutes"), isFalse, reason: "onMs=0 正文不写「已亮 N 分钟」");
      expect(p.containsKey("foreground_app"), isFalse);
      expect(p.containsKey("confidence"), isFalse, reason: "A24 不新增采集、不猜置信度");
    });

    test("有值时按正文言归一（分钟四舍五入、电量取整）", () {
      final p = jsonDecode(
        PhonePerceptionService.buildShizukuPayload({
          "screenOn": true,
          "screenOnMs": 150000,
          "foregroundApp": "com.android.chrome",
          "batteryLevel": 80.6,
          "batteryCharging": true,
          "network": "5G",
          "dnd": true,
          "device": "V2507A",
          "androidVersion": "16",
        })!,
      ) as Map;
      expect(p["screen_on_minutes"], 3, reason: "150000ms → 正文「已亮 3 分钟」");
      expect(p["battery_percent"], 80);
      expect(p["battery_charging"], isTrue);
      expect(p["foreground_app"], "com.android.chrome");
      expect(p.keys.length, 9);
    });

    test("全空快照只剩正文也写的那两条：屏幕/勿扰（不另造内容，也不虚构其它字段）", () {
      final p = jsonDecode(
        PhonePerceptionService.buildShizukuPayload({})!,
      ) as Map;
      expect(
        p,
        {"dnd": false, "screen_on": false},
        reason: "formatSnapshot 对空 data 同样输出「屏幕熄灭；勿扰：关闭」，载荷跟着正文走；"
            "真正的空快照在调用方就被 ok!=true 拦掉了（见 uploadShizukuSnapshotIfAvailable）",
      );
    });
  });

  group("clientKeyOf：幂等键", () {
    test("无载荷时与 A24 之前的旧格式逐字一致（老队列 / 老调用方的兼容锁）", () {
      const source = "clipboard";
      const content = "  你好世界  ";
      final legacy =
          "$source|${content.trim().length}|${content.trim().hashCode.abs()}";
      expect(PerceptionOutbox.clientKeyOf(source, content), legacy);
      expect(PerceptionOutbox.clientKeyOf(source, content, null), legacy);
      expect(PerceptionOutbox.clientKeyOf(source, content, "   "), legacy,
          reason: "空白载荷＝无载荷");
    });

    test("同正文不同载荷是两个键（服务端把载荷纳入 5 分钟去重）", () {
      const content = "最近相册：a.jpg";
      final k1 = PerceptionOutbox.clientKeyOf("media", content, '{"count":1}');
      final k2 = PerceptionOutbox.clientKeyOf("media", content, '{"count":2}');
      expect(k1, isNot(k2));
      expect(
        k1,
        startsWith("${PerceptionOutbox.clientKeyOf("media", content)}|p"),
      );
      expect(
        k1,
        PerceptionOutbox.clientKeyOf("media", content, '{"count":1}'),
        reason: "同一份载荷仍要可重复（去重靠它）",
      );
    });
  });

  group("载荷真的走到线上", () {
    test("入队存 payload 字段，flush 补传时带 payload_json", () async {
      const payload = '{"count":2,"items":[{"name":"a.jpg"}]}';
      await PerceptionOutbox.enqueue(
        source: "media",
        content: "最近相册：a.jpg、b.jpg",
        payload: payload,
      );
      expect(queueFiles(), hasLength(1));
      expect(
        (jsonDecode(queueFiles().single.readAsStringSync()) as Map)["payload"],
        payload,
      );

      final forms = captureForms(FakeApiAdapter());
      expect(await PerceptionOutbox.flush(), 1);
      expect(forms.single["payload_json"], payload);
      expect(forms.single["client_key"],
          PerceptionOutbox.clientKeyOf("media", "最近相册：a.jpg、b.jpg", payload));
      expect(await PerceptionOutbox.pendingCount(), 0);
    });

    test("老队列文件（没有 payload 字段）补传时不带 payload_json", () async {
      dirOf().createSync(recursive: true);
      await File("${dirOf().path}/legacy.json").writeAsString(jsonEncode({
        "source": "clipboard",
        "content": "老数据",
        "clientKey": "clipboard|3|111",
        "createdAtMs": PerceptionOutbox.nowProvider().millisecondsSinceEpoch,
        "attempts": 0,
        "seq": 1,
      }));

      final forms = captureForms(FakeApiAdapter());
      expect(await PerceptionOutbox.flush(), 1);
      expect(forms.single.containsKey("payload_json"), isFalse);
    });

    test("uploadSnapshot 直传带 payload_json，成功后从队列摘掉", () async {
      const payload = '{"text":"你好"}';
      final forms = captureForms(FakeApiAdapter());

      final ok = await PhonePerceptionService.uploadSnapshot(
        "你好",
        "clipboard",
        payload: payload,
      );
      expect(ok, isTrue);
      expect(forms.single["payload_json"], payload);
      expect(await PerceptionOutbox.pendingCount(), 0);

      // 不带载荷的调用方：请求里不出现该字段（与 A24 之前逐字一致）
      await PhonePerceptionService.uploadSnapshot("第二条", "clipboard");
      expect(forms.last.containsKey("payload_json"), isFalse);
    });

    test("uploadActionResult 也走同一出口：带字段载荷、进本地队列、成功后摘掉", () async {
      final forms = captureForms(FakeApiAdapter());

      final ok = await PhonePerceptionService.uploadActionResult(
          "click", "微信", true, "多余的原文");
      expect(ok, isTrue);
      final f = forms.single;
      expect(f["source"], "action_result");
      expect(f["payload_json"], isNotNull, reason: "此前它直接 dio.post，没有载荷也没有 client_key");
      expect(f["client_key"], isNotNull);
      final p = jsonDecode(f["payload_json"]!) as Map;
      expect(p["target"], "微信");
      expect(p["ok"], isTrue);
      expect(p.containsKey("message"), isFalse, reason: "成功时正文不写原因，载荷也不写");
      expect(await PerceptionOutbox.pendingCount(), 0);

      await PhonePerceptionService.uploadActionResult(
          "scroll", "抖音", false, "节点找不到");
      expect(
        jsonDecode(forms.last["payload_json"]!) as Map,
        containsPair("message", "节点找不到"),
      );
    });

    test("同正文不同载荷在队列里是两条，不互相吞掉", () async {
      await PerceptionOutbox.enqueue(
          source: "media", content: "最近相册：a.jpg", payload: '{"count":1}');
      await PerceptionOutbox.enqueue(
          source: "media", content: "最近相册：a.jpg", payload: '{"count":2}');
      expect(await PerceptionOutbox.pendingCount(), 2);
      // 同正文同载荷重复入队才去重
      await PerceptionOutbox.enqueue(
          source: "media", content: "最近相册：a.jpg", payload: '{"count":1}');
      expect(await PerceptionOutbox.pendingCount(), 2);
    });
  });
}
