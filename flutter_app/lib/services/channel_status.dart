import "dart:async";
import "dart:convert";
import "package:shared_preferences/shared_preferences.dart";

/// 设备级通道状态记录器（2026-09-21 P1：通道状态与错误码化）—— 只做观测，不参与控制流。
///
/// 背景：此前 Dart 层大面积 `catch (_) {}` 一律返回空值，调用方分不清「本来就没数据」和
/// 「通道坏了」。这里把每条通道的「上次成功时间 / 上次失败时间 / 连续失败次数 / 错误码」
/// 记下来，供诊断与后续 UI 使用。
///
/// 隐私说明：本记录器**只存设备级通道健康信息**（时间戳 / 失败计数 / 错误码 / 简短原因），
/// **不含任何用户内容、不含账号数据**，因此不涉及多账号隔离；通道名一律使用下面的固定
/// 字符串常量，禁止拼接 user_id 或任何账号 / 角色标识。
enum ChannelCode {
  ok,
  noPermission,
  serverDown,
  timeout,
  deadObject,
  networkError,
  empty,
  unknown,
}

/// 单条通道的状态（可序列化，落 SharedPreferences）
class ChannelState {
  final ChannelCode code;
  final bool retriable;
  final DateTime? lastOkAt;
  final DateTime? lastErrorAt;
  final int failCount;
  final String detail;

  const ChannelState({
    required this.code,
    this.retriable = false,
    this.lastOkAt,
    this.lastErrorAt,
    this.failCount = 0,
    this.detail = "",
  });

  Map<String, dynamic> toJson() => {
        "code": code.name,
        "retriable": retriable,
        "lastOkAt": lastOkAt?.toIso8601String(),
        "lastErrorAt": lastErrorAt?.toIso8601String(),
        "failCount": failCount,
        "detail": detail,
      };

  factory ChannelState.fromJson(Map<String, dynamic> j) => ChannelState(
        code: ChannelCode.values.firstWhere(
          (e) => e.name == (j["code"]?.toString() ?? ""),
          orElse: () => ChannelCode.unknown,
        ),
        retriable: j["retriable"] == true,
        lastOkAt: DateTime.tryParse(j["lastOkAt"]?.toString() ?? ""),
        lastErrorAt: DateTime.tryParse(j["lastErrorAt"]?.toString() ?? ""),
        failCount: (j["failCount"] as num?)?.toInt() ?? 0,
        detail: j["detail"]?.toString() ?? "",
      );
}

/// 通道状态记录器：内存缓存 + SharedPreferences 持久化（key 前缀 `chan_status_`）
class ChannelStatusTracker {
  ChannelStatusTracker._();

  /// SharedPreferences key 前缀（值为 ChannelState 的 JSON 字符串）
  static const String keyPrefix = "chan_status_";

  // === 固定通道名（设备级，禁止拼 user_id，见文件头隐私说明）===
  static const String kShizukuStatus = "shizuku_status";
  static const String kShizukuShell = "shizuku_shell";
  static const String kShizukuAppList = "shizuku_applist";
  static const String kShizukuSnapshot = "shizuku_snapshot";
  static const String kAccessibility = "accessibility";
  static const String kNotification = "notification";
  static const String kServiceHealth = "service_health";
  static const String kPerceptionUpload = "perception_upload";

  static final Map<String, ChannelState> _cache = {};

  /// 成功一次：刷新 lastOkAt、失败计数归零、清 detail（lastErrorAt 保留备查）
  static Future<void> recordOk(String channel) async {
    final prev = _cache[channel] ?? await _read(channel);
    final next = ChannelState(
      code: ChannelCode.ok,
      lastOkAt: DateTime.now(),
      lastErrorAt: prev?.lastErrorAt,
      failCount: 0,
    );
    _cache[channel] = next;
    await _write(channel, next);
  }

  /// 失败一次：记 lastErrorAt、failCount++；**lastOkAt 保留不清**（连续失败期间
  /// 仍要知道「上次成功是什么时候」，这正是要观测的东西）
  static Future<void> recordFail(
    String channel,
    ChannelCode code, {
    String detail = "",
    bool retriable = false,
  }) async {
    final prev = _cache[channel] ?? await _read(channel);
    final next = ChannelState(
      code: code,
      retriable: retriable,
      lastOkAt: prev?.lastOkAt,
      lastErrorAt: DateTime.now(),
      failCount: (prev?.failCount ?? 0) + 1,
      detail: detail,
    );
    _cache[channel] = next;
    await _write(channel, next);
  }

  /// 同步读取：命中内存缓存直接返回；未命中时异步回读 SharedPreferences
  /// （结果回填缓存，下次调用即可命中），本次返回 null。
  static ChannelState? of(String channel) {
    final hit = _cache[channel];
    if (hit != null) return hit;
    unawaited(_warm(channel));
    return null;
  }

  /// 全量快照（供诊断 / 后续 UI）：channel -> {code, retriable, lastOkAt, ...}
  static Future<Map<String, dynamic>> snapshotAll() async {
    final p = await SharedPreferences.getInstance();
    final out = <String, dynamic>{};
    for (final k in p.getKeys()) {
      if (!k.startsWith(keyPrefix)) continue;
      final s = _decode(p.getString(k));
      if (s == null) continue;
      final channel = k.substring(keyPrefix.length);
      out[channel] = (_cache[channel] ?? s).toJson();
    }
    for (final e in _cache.entries) {
      out.putIfAbsent(e.key, () => e.value.toJson());
    }
    return out;
  }

  /// native（ShizukuBridge）返回的中文提示文案 → 错误码
  static ChannelCode classifyShizukuStderr(String stderr) {
    if (stderr.contains("服务未运行")) return ChannelCode.serverDown;
    if (stderr.contains("未获得 Shizuku 授权") || stderr.contains("权限不足")) {
      return ChannelCode.noPermission;
    }
    if (stderr.contains("连接异常") ||
        stderr.contains("DeadObject") ||
        stderr.contains("process hasn't exited")) {
      return ChannelCode.deadObject;
    }
    if (stderr.contains("超时")) return ChannelCode.timeout;
    return ChannelCode.unknown;
  }

  /// native（ShizukuBridge）返回的结构化错误码 → 错误码；未知或空一律 unknown
  /// （2026-09-21 P3：native 侧已给 code 时优先用它，不再靠中文文案猜）
  static ChannelCode codeFromNative(String? code) {
    switch (code) {
      case "serverDown":
        return ChannelCode.serverDown;
      case "noPermission":
        return ChannelCode.noPermission;
      case "deadObject":
        return ChannelCode.deadObject;
      case "timeout":
        return ChannelCode.timeout;
      case "ok":
        return ChannelCode.ok;
      default:
        return ChannelCode.unknown;
    }
  }

  /// Dart 异常 → 错误码
  static ChannelCode classifyException(Object e) {
    if (e is TimeoutException) return ChannelCode.timeout;
    final s = e.toString();
    if (s.contains("SocketException") ||
        s.contains("Connection") ||
        s.contains("Failed host lookup")) {
      return ChannelCode.networkError;
    }
    return ChannelCode.unknown;
  }

  static Future<void> _warm(String channel) async {
    final s = await _read(channel);
    if (s != null) _cache[channel] = s;
  }

  static Future<ChannelState?> _read(String channel) async {
    final p = await SharedPreferences.getInstance();
    return _decode(p.getString("$keyPrefix$channel"));
  }

  static Future<void> _write(String channel, ChannelState s) async {
    final p = await SharedPreferences.getInstance();
    await p.setString("$keyPrefix$channel", jsonEncode(s.toJson()));
  }

  static ChannelState? _decode(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    try {
      final m = jsonDecode(raw);
      if (m is! Map) return null;
      return ChannelState.fromJson(Map<String, dynamic>.from(m));
    } catch (_) {
      return null;
    }
  }
}
