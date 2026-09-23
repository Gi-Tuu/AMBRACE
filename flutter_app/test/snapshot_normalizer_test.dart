import "dart:convert";
import "dart:io";

import "package:flutter_test/flutter_test.dart";

import "package:ai_companion/services/snapshot_normalizer.dart";

/// X7-M2 归一化层 fixture 回归（派单 P10 §2.3）。
///
/// 样本目录 test/fixtures/ 下三份合成样本（AOSP / vivo / 残缺），文件头 `_note` 标注不含真实
/// 用户数据；断言字段值、confidence、rawRef，以及缺失字段在三处都不出现。

Map<String, dynamic> _load(String name) {
  final text = File("test/fixtures/$name").readAsStringSync();
  return Map<String, dynamic>.from(jsonDecode(text) as Map);
}

void main() {
  test("AOSP 风格：全字段走主正则，confidence 均 1.0", () {
    final s = normalizeShizukuSnapshot(_load("aosp_snapshot.json"));
    expect(s.fields, {
      "foregroundApp": "com.android.settings",
      "screenOn": true,
      "screenOnMs": 123456,
      "batteryLevel": 87,
      "batteryCharging": true,
      "network": "WIFI",
      "dnd": false,
      "device": "Google Pixel 7",
      "androidVersion": "14",
    });
    expect(s.confidence.length, 9);
    expect(s.confidence.values.every((double c) => c == kConfDirect), isTrue);
    expect(s.rawRef, {
      "foregroundApp": "activity",
      "screenOn": "power",
      "screenOnMs": "power",
      "batteryLevel": "battery",
      "batteryCharging": "battery",
      "network": "connectivity",
      "dnd": "zen",
      "device": "manufacturer+model",
      "androidVersion": "android",
    });
    expect(s.toJson().keys, containsAll(<String>["fields", "confidence", "rawRef"]));
  });

  test("vivo 风格：前台启发式①=0.6、缺 Transports 网络启发式②=0.6、power 文案略异仍 1.0", () {
    final s = normalizeShizukuSnapshot(_load("vivo_snapshot.json"));
    // 启发式①：mResumedActivity=ComponentInfo{pkg/Act} 取包名，记 0.6
    expect(s.fields["foregroundApp"], "com.bbk.launcher2");
    expect(s.confidence["foregroundApp"], kConfHeuristic);
    expect(s.rawRef["foregroundApp"], "activity");
    // power 文案略异（缩进/顺序不同），主正则仍命中 → 1.0
    expect(s.fields["screenOn"], true);
    expect(s.confidence["screenOn"], kConfDirect);
    expect(s.fields["screenOnMs"], 456789);
    expect(s.confidence["screenOnMs"], kConfDirect);
    expect(s.fields["batteryLevel"], 55);
    expect(s.fields["batteryCharging"], false); // status: 3 非充电
    // 启发式②：无 Transports: 回落关键字 WIFI，记 0.6
    expect(s.fields["network"], "WIFI");
    expect(s.confidence["network"], kConfHeuristic);
    expect(s.rawRef["network"], "connectivity");
    expect(s.fields["dnd"], true); // zen=1
    expect(s.fields["device"], "vivo V2507A");
    expect(s.fields["androidVersion"], "16");
  });

  test("残缺输入：只有 battery + zen，其余字段在 fields/confidence/rawRef 三处都不出现", () {
    final s = normalizeShizukuSnapshot(_load("partial_snapshot.json"));
    expect(s.fields, {"batteryLevel": 20, "batteryCharging": true, "dnd": false});
    expect(s.confidence, {
      "batteryLevel": kConfDirect,
      "batteryCharging": kConfDirect,
      "dnd": kConfDirect,
    });
    expect(s.rawRef, {
      "batteryLevel": "battery",
      "batteryCharging": "battery",
      "dnd": "zen",
    });
    for (final missing in [
      "foregroundApp",
      "screenOn",
      "screenOnMs",
      "network",
      "device",
      "androidVersion",
    ]) {
      expect(s.fields.containsKey(missing), isFalse, reason: "fields 不应含 $missing");
      expect(s.confidence.containsKey(missing), isFalse, reason: "confidence 不应含 $missing");
      expect(s.rawRef.containsKey(missing), isFalse, reason: "rawRef 不应含 $missing");
    }
  });

  test("M2b 接线：native 风格整包用 raw 归一化出值；缺 raw 时保持空 normalized", () {
    // 模拟 native getSystemSnapshot 的最终回调：data 是解析后 map，raw 是原始 dumpsys/getprop 文本
    final payload = <String, dynamic>{
      "ok": true,
      "data": <String, dynamic>{
        "foregroundApp": "com.bbk.launcher2",
        "screenOn": true,
        "batteryLevel": 55,
        "network": "WIFI",
        "dnd": true,
        "device": "vivo V2507A",
        "androidVersion": "16",
      },
      "raw": _load("vivo_snapshot.json"),
    };

    final s = normalizeSnapshotPayload(payload);
    expect(s.fields.isNotEmpty, isTrue, reason: "接线后 normalized 不该再是空的");
    expect(s.fields["foregroundApp"], "com.bbk.launcher2");
    expect(s.fields["batteryLevel"], 55);
    expect(s.confidence["foregroundApp"], kConfHeuristic);
    expect(s.rawRef["foregroundApp"], "activity");

    // 旧版 native（只回解析后 data、没有 raw）：normalized 仍为空，与接线前行为一致
    final legacy = normalizeSnapshotPayload(<String, dynamic>{
      "ok": true,
      "data": (payload["data"] as Map).cast<String, dynamic>(),
    });
    expect(legacy.fields, isEmpty);
    expect(legacy.confidence, isEmpty);
    expect(legacy.rawRef, isEmpty);
  });
}
