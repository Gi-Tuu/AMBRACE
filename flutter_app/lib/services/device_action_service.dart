import "package:dio/dio.dart";
import "api_client.dart";

/// X7-M4b-2 内置行动通道（App 侧）——**纯 API 封装**：提交意图 / 取待办 / 回报结果。
///
/// 只做网络层，不做判定也不执行任何动作（判定与执行在 `device_action_executor.dart`）。
/// 内置身份由**服务端端点**固定（`POST /api/v1/device/actions` 恒为内置，M4c-1 身份收口）：
/// 请求体**不得携带 `plugin`**（带了会被拒 `invalid_intent:plugin_not_allowed`，接口不接受自称），
/// 但全局开关 / 账号开关 / 目标白名单 / 限流熔断一条不少，所以被拒是常态而非异常。
/// 所有异常一律折成结构化结果返回，绝不把异常抛给 UI。

/// 一次提交（裁决）的结果。`reason` 恒为后端机器可读字面量，原样展示即可定位被哪层闸门挡住。
class ActionSubmitResult {
  final bool allowed;
  final String reason;
  final String status;
  final bool dryRun;
  final String actionToken;

  const ActionSubmitResult({
    required this.allowed,
    this.reason = "",
    this.status = "",
    this.dryRun = false,
    this.actionToken = "",
  });

  bool get failed => !allowed;
}

/// `GET .../pending` 的结果：`error` 非空表示**没拿到列表**（断网/未登录），
/// 与「拿到了空列表」必须区分开——否则 UI 会把「网络失败」说成「没有待办动作」。
class PendingActions {
  final List<Map<String, dynamic>> items;
  final String error;

  const PendingActions(this.items, {this.error = ""});

  bool get isEmpty => items.isEmpty;
}

class DeviceActionService {
  static const String _submitPath = "/api/v1/device/actions";
  static const String _pendingPath = "/api/v1/device/actions/pending";

  /// 提交一条行动意图 → 后端四层闸门裁决。
  ///
  /// 只带该意图实际用到的字段：后端 `ActionIntent` 是 `extra="forbid"` 且按能力 schema
  /// 逐字段对账，多传（例如给 `action_open_app` 传空 `text`）会被判 `unexpected_field`。
  static Future<ActionSubmitResult> submitIntent({
    required String capability,
    required String targetApp,
    String? by,
    String? query,
    String? text,
    bool dryRun = false,
  }) async {
    // M4c-1 身份收口：内置身份由后端端点固定，请求体带 plugin 会被拒（不接受自称）
    final body = <String, dynamic>{
      "capability": capability,
      "target_app": targetApp,
      "dry_run": dryRun,
    };
    void putIfPresent(String key, String? value) {
      if (value != null && value.trim().isNotEmpty) body[key] = value;
    }

    putIfPresent("by", by);
    putIfPresent("query", query);
    putIfPresent("text", text);
    try {
      final r = await ApiClient().dio.post(_submitPath, data: body);
      final data = r.data is Map ? Map<String, dynamic>.from(r.data as Map) : const <String, dynamic>{};
      return ActionSubmitResult(
        allowed: data["allowed"] == true,
        reason: data["reason"]?.toString() ?? "",
        status: data["status"]?.toString() ?? "",
        dryRun: data["dry_run"] == true,
        actionToken: data["action_token"]?.toString() ?? "",
      );
    } catch (e) {
      return ActionSubmitResult(allowed: false, reason: _transportReason(e), dryRun: dryRun);
    }
  }

  /// 取当前账号已被批准、待执行的意图列表。
  static Future<PendingActions> fetchPending() async {
    try {
      final r = await ApiClient().dio.get(_pendingPath);
      final list = r.data is List ? r.data as List : const [];
      return PendingActions(
        list.whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList(),
      );
    } catch (e) {
      return PendingActions(const [], error: _transportReason(e));
    }
  }

  /// 回报执行结果（服务端据此出队 + 累计连续失败次数驱动熔断）。
  /// 返回 true ＝ 服务端确认收到该 token 的回报；异常/未命中一律 false，不抛给调用方。
  static Future<bool> reportResult(String token, {required bool ok, String detail = ""}) async {
    if (token.isEmpty) return false;
    try {
      final r = await ApiClient()
          .dio.post("/api/v1/device/actions/$token/result", data: {"ok": ok, "detail": detail});
      final data = r.data is Map ? Map<String, dynamic>.from(r.data as Map) : const <String, dynamic>{};
      return data["ok"] == true;
    } catch (_) {
      return false;
    }
  }

  /// 传输层失败折成机器可读 reason（保留后端 reason 与网络失败两种口径，便于自检分辨）。
  static String _transportReason(Object e) {
    if (e is DioException) return "request_failed:${e.type.name}";
    return "request_failed:$e";
  }
}
