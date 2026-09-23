// X7-M2 归一化层：把 native `ShizukuBridge.parseSystemSnapshot` 的 `dumpsys`/`getprop`
// 解析口径平移到 Dart 侧一个纯函数（可 fixture 测试），并给每个字段补 `confidence` 与来源 `rawRef`。
//
// 纯 Dart，不 import 任何插件：入参 `rawData` 就是 native 回给 `ShizukuService.getSystemSnapshot()`
// 的那个 `raw`（M2b 起随包回传；键：`activity` / `power` / `battery` / `connectivity` / `zen` /
// `manufacturer` / `model` / `android`，值为 `dumpsys`/`getprop` 原始文本）。
//
// 置信度约定（见派单 §2.1）：正则命中且值可解析＝1.0；靠补充启发式命中＝0.6；缺失＝整条不出现
// （`fields` / `confidence` / `rawRef` 三处都不写）。`dnd` / `device` / `androidVersion` 是
// `settings get` / `getprop` 的直读（无正则、也非启发式），按 1.0 计。

/// 正则命中（含直读）的可信置信度。
const double kConfDirect = 1.0;

/// 靠补充 ROM 启发式命中时的置信度。
const double kConfHeuristic = 0.6;

// ── 正则口径：逐条从 ShizukuBridge.parseSystemSnapshot 平移，语义不许变 ──
final RegExp _fgPrimaryRe = RegExp(r'topResumedActivity=ActivityRecord\{[^}]*?\s+([^\s/]+)/');
final RegExp _wakeRe = RegExp(r'mWakefulness=([A-Za-z]+)');
final RegExp _screenOnTimeRe = RegExp(r'mScreenOnTime=([0-9]+)');
final RegExp _levelRe = RegExp(r'^\s*level: (\d+)', multiLine: true);
final RegExp _statusRe = RegExp(r'^\s*status: (\d+)', multiLine: true);
final RegExp _transportsRe = RegExp(r'Transports: ([A-Z_]+)');

// ── 补充启发式（native 现在没有，命中记 kConfHeuristic）──
// ① 前台应用回落：native 只认 `topResumedActivity=ActivityRecord{...}`，但不少 ROM（部分三星 /
//    MIUI / 老版 AOSP，以及 vivo 的部分桌面态）用 `mResumedActivity=` 或 `topResumedActivity=`
//    后接 `ComponentInfo{pkg/Act}` 形式报前台。这里放宽成「标记后第一个 包名/Activity 形态」，
//    只取包名段，覆盖 ActivityRecord 结构对不上、但组件信息仍在的那类 ROM。
final RegExp _fgFallbackRe = RegExp(r'(?:mResumedActivity=|topResumedActivity=)[^\n]*?([A-Za-z0-9_.]+)/');
// ② 网络回落：native 只读 `Transports:`，但部分 ROM（含 vivo）的 connectivity dump 不打这行，
//    活跃网络信息散落在 `NetworkAgentInfo{... WIFI ...}` 等文本里。缺 Transports 时按关键字
//    WIFI/CELLULAR 判活跃承载，覆盖面更宽但更弱，故记 0.6。
final RegExp _netKeywordRe = RegExp(r'WIFI|CELLULAR');

/// 归一化后的手机快照：字段值 + 每字段置信度 + 每字段来源键。
class NormalizedSnapshot {
  NormalizedSnapshot({
    required this.fields,
    required this.confidence,
    required this.rawRef,
  });

  /// 字段值（键与 [ShizukuService.formatSnapshot] 消费的完全对齐）。缺失字段不出现在此表。
  final Map<String, Object> fields;

  /// 每字段置信度（1.0 正则命中 / 0.6 启发式）。仅对 `fields` 里出现的字段写入。
  final Map<String, double> confidence;

  /// 每字段来源键名（如 `foregroundApp -> activity`）。仅对 `fields` 里出现的字段写入。
  final Map<String, String> rawRef;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'fields': fields,
        'confidence': confidence,
        'rawRef': rawRef,
      };
}

/// native 回传文本 → 归一化字段（可 fixture 回归；不产文案，文案仍走 formatSnapshot）。
NormalizedSnapshot normalizeShizukuSnapshot(Map<String, dynamic> rawData) {
  final fields = <String, Object>{};
  final confidence = <String, double>{};
  final rawRef = <String, String>{};

  void put(String field, Object value, double conf, String ref) {
    fields[field] = value;
    confidence[field] = conf;
    rawRef[field] = ref;
  }

  // 前台应用：dumpsys activity activities 的 topResumedActivity（回落见启发式①）
  final act = _text(rawData['activity']);
  final fgPrimary = _fgPrimaryRe.firstMatch(act);
  if (fgPrimary != null) {
    put('foregroundApp', fgPrimary.group(1)!, kConfDirect, 'activity');
  } else {
    final fgFallback = _fgFallbackRe.firstMatch(act);
    if (fgFallback != null) {
      put('foregroundApp', fgFallback.group(1)!, kConfHeuristic, 'activity');
    }
  }

  // 屏幕：mWakefulness=Awake|On（native 同口径，其余值一律 screenOn=false）
  final power = _text(rawData['power']);
  final wake = _wakeRe.firstMatch(power);
  if (wake != null) {
    final w = wake.group(1)!;
    put('screenOn', w == 'Awake' || w == 'On', kConfDirect, 'power');
  }
  // 亮屏时长：mScreenOnTime=xxx (ms)
  final onMs = int.tryParse(_screenOnTimeRe.firstMatch(power)?.group(1) ?? '');
  if (onMs != null) {
    put('screenOnMs', onMs, kConfDirect, 'power');
  }

  // 电池：level / status（2=充电中 5=充满）
  final bat = _text(rawData['battery']);
  final level = int.tryParse(_levelRe.firstMatch(bat)?.group(1) ?? '');
  if (level != null) {
    put('batteryLevel', level, kConfDirect, 'battery');
  }
  final status = _statusRe.firstMatch(bat)?.group(1);
  if (status != null) {
    put('batteryCharging', status == '2' || status == '5', kConfDirect, 'battery');
  }

  // 网络：Transports: CELLULAR/WIFI（回落见启发式②）
  final conn = _text(rawData['connectivity']);
  final transports = _transportsRe.firstMatch(conn);
  if (transports != null) {
    put('network', transports.group(1)!.toUpperCase(), kConfDirect, 'connectivity');
  } else {
    final keyword = _netKeywordRe.firstMatch(conn);
    if (keyword != null) {
      put('network', keyword.group(0)!.toUpperCase(), kConfHeuristic, 'connectivity');
    }
  }

  // 勿扰：settings get global zen_mode（直读，键存在即出值——与 native `?.trim()` 判定同口径）
  if (rawData.containsKey('zen')) {
    final zen = _text(rawData['zen']).trim();
    put('dnd', zen.isNotEmpty && zen != '0', kConfDirect, 'zen');
  }

  // 设备：manufacturer + model（过滤空与 unknown，空格拼接）
  final device = <String>[
    _text(rawData['manufacturer']).trim(),
    _text(rawData['model']).trim(),
  ].where((e) => e.isNotEmpty && e != 'unknown').join(' ');
  if (device.isNotEmpty) {
    put('device', device, kConfDirect, 'manufacturer+model');
  }

  // Android 版本：ro.build.version.release（直读）
  final ver = _text(rawData['android']).trim();
  if (ver.isNotEmpty) {
    put('androidVersion', ver, kConfDirect, 'android');
  }

  return NormalizedSnapshot(fields: fields, confidence: confidence, rawRef: rawRef);
}

String _text(Object? v) => v == null ? '' : v.toString();

/// M2b 接线：从 native `shizukuSystemSnapshot` 的整包回传里取原始文本表并归一化。
///
/// 只有 `raw`（未解析的 dumpsys/getprop 原文）能喂给 [normalizeShizukuSnapshot]；
/// 没有 `raw`（旧版 native / 出错回包）时回空表，`normalized` 与接线前一样是空的。
NormalizedSnapshot normalizeSnapshotPayload(Map<String, dynamic> payload) {
  final raw = payload['raw'];
  return normalizeShizukuSnapshot(
    raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{},
  );
}
