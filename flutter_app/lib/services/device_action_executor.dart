import "dart:io";
import "device_action_prefs.dart";
import "device_action_service.dart";
import "phone_perception_service.dart";
import "shizuku_service.dart";

/// X7-M4b-2 内置行动执行器：把**已被后端闸门批准**的意图翻译成**既有**能力调用并回报。
///
/// 纯逻辑（[isSafePackageName] / [blockedReason] / [needsConfirm] / [shouldConfirm] / [runOnce] 的编排）与
/// 真机执行（[executeOnDevice]）严格分离：`runOnce` 的 `execute` / `fetchPending` / `report`
/// 与三档策略（`policyOf` / `onceEverConfirmedOf` / `markOnceEverConfirmed`）都可注入，
/// 单测因此完全不碰网络、MethodChannel 与真 prefs。
///
/// 三条硬规矩：
/// ① 静态校验不过一律不执行；`dry_run` 既不执行也不回报；
/// ② 前置不满足（无障碍未开 / Shizuku 未运行或未授权 / 非 Android）→ 如实回报 `ok:false`，
///    让服务端连续失败计数（熔断）起作用，**禁止假装成功**；
/// ③ 每条动作自带 try/catch：单条出错不影响后续条目。
typedef ActionExecuteFn = Future<Map<String, dynamic>> Function(Map<String, dynamic> intent);
typedef ActionConfirmFn = Future<bool> Function(String capability);
typedef ActionReportFn = Future<bool> Function(String token, bool ok, String detail);

// M4c-5 三档策略的注入位（默认走 DeviceActionPrefs；单测传假函数，不碰真 prefs）
typedef ActionPolicyFn = Future<ActionConfirmPolicy> Function(String capability);
typedef ActionOnceEverCheckFn = Future<bool> Function(String capability);
typedef ActionMarkOnceEverFn = Future<bool> Function(String capability);

class DeviceActionExecutor {
  // ── 首批三条行动能力（与后端 app/device/capabilities.py 的 id 字面量一致，勿改名）──
  static const String capOpenApp = "action_open_app";
  static const String capTap = "action_tap";
  static const String capSetText = "action_set_text";
  static const Set<String> knownCapabilities = {capOpenApp, capTap, capSetText};

  // ── 拒绝原因：机器可读字面量，同时作为回报给服务端的 detail（两处同一口径）──
  static const String reasonDryRun = "dry_run_skipped";
  static const String reasonUnsupported = "unsupported_capability:";
  static const String reasonNoTarget = "missing_target_app";
  static const String reasonUnsafeTarget = "unsafe_target_app";
  static const String reasonBadBy = "bad_by";
  static const String reasonNoQuery = "missing_query";
  static const String reasonNoText = "missing_text";

  // ── 单条处理结论 ──
  static const String statusExecuted = "executed_ok";
  static const String statusFailed = "executed_failed";
  static const String statusBlocked = "blocked";
  static const String statusDryRun = "dry_run_skipped";
  static const String statusConfirmDenied = "confirm_denied";
  static const String statusFetchFailed = "fetch_failed";

  static const int maxPackageNameLength = 128;
  static const int maxDetailLength = 200;

  /// 包名形态（与后端 M4b-1 白名单校验同一条正则）：至少一段点号、逐段合法、≤128 字符。
  static final RegExp _packageNameRe = RegExp(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$");

  /// 严格包名判定：不 trim、不放宽任何字符——通不过就**绝不**进 shell。
  static bool isSafePackageName(String raw) {
    if (raw.isEmpty || raw.length > maxPackageNameLength) return false;
    return _packageNameRe.hasMatch(raw);
  }

  /// 静态校验：返回不执行的原因，`null` ＝ 允许执行。
  static String? blockedReason(Map<String, dynamic> intent) {
    if (intent["dry_run"] == true) return reasonDryRun;
    final capability = intent["capability"]?.toString() ?? "";
    if (!knownCapabilities.contains(capability)) return "$reasonUnsupported$capability";
    final targetApp = intent["target_app"]?.toString() ?? "";
    if (targetApp.trim().isEmpty) return reasonNoTarget;
    if (capability == capOpenApp) {
      return isSafePackageName(targetApp) ? null : reasonUnsafeTarget;
    }
    if (capability == capTap) {
      final by = intent["by"]?.toString() ?? "";
      if (by != "text" && by != "id") return reasonBadBy;
      return (intent["query"]?.toString() ?? "").trim().isEmpty ? reasonNoQuery : null;
    }
    // action_set_text
    return (intent["text"]?.toString() ?? "").trim().isEmpty ? reasonNoText : null;
  }

  /// 决策④：每类动作首次确认、之后放行（确认集合按 capability 记账，跨轮次复用同一 Set 即可）。
  static bool needsConfirm(String capability, Set<String> confirmedTypes) =>
      !confirmedTypes.contains(capability);

  /// M4c-5 三档判定（纯函数，可单测）：
  /// 轻＝只认「永久放行」标记；中＝沿用会话内集合（＝[needsConfirm]，现状语义）；重＝恒确认。
  static bool shouldConfirm(
    String capability,
    ActionConfirmPolicy policy, {
    required bool onceEverConfirmed,
    required Set<String> sessionConfirmed,
  }) {
    switch (policy) {
      case ActionConfirmPolicy.onceEver:
        return !onceEverConfirmed;
      case ActionConfirmPolicy.firstPerType:
        return needsConfirm(capability, sessionConfirmed);
      case ActionConfirmPolicy.everyTime:
        return true;
    }
  }

  /// 拉一次待办并逐条处理：静态校验 → 按档位确认 → 执行 → 回报。
  ///
  /// 返回值是每条一行的处理台账（顺序与 pending 一致；传 [onlyTokens] 时只含命中的条目），供 UI 原样展示。
  ///
  /// [onlyTokens] ＝「只执行本条」的名单：非 null 时仅处理 `action_token` 命中的条目，其余条目**连台账都不出**
  /// （不执行、不回报＝原样留在服务端队列，下次照常可取）；缺省 `null` ＝处理拉到的全部待办。
  static Future<List<Map<String, dynamic>>> runOnce({
    required ActionConfirmFn confirm,
    ActionExecuteFn? execute,
    Set<String>? confirmedTypes,
    Set<String>? onlyTokens,
    Future<PendingActions> Function()? fetchPending,
    ActionReportFn? report,
    ActionPolicyFn? policyOf,
    ActionOnceEverCheckFn? onceEverConfirmedOf,
    ActionMarkOnceEverFn? markOnceEverConfirmed,
  }) async {
    final confirmed = confirmedTypes ?? <String>{};
    final doExecute = execute ?? executeOnDevice;
    final doFetch = fetchPending ?? DeviceActionService.fetchPending;
    final doReport = report ?? _serviceReport;
    final doPolicy = policyOf ?? DeviceActionPrefs.policyFor;
    final doOnceEver = onceEverConfirmedOf ?? DeviceActionPrefs.isOnceEverConfirmed;
    final doMark = markOnceEverConfirmed ?? DeviceActionPrefs.markOnceEverConfirmed;

    final PendingActions pending;
    try {
      pending = await doFetch();
    } catch (e) {
      return [_row("", "", statusFetchFailed, _clip("fetch_error:$e"), false, false, false)];
    }
    if (pending.error.isNotEmpty) {
      return [_row("", "", statusFetchFailed, _clip(pending.error), false, false, false)];
    }
    if (pending.items.isEmpty) return const [];

    final results = <Map<String, dynamic>>[];
    for (final intent in pending.items) {
      final token = intent["action_token"]?.toString() ?? "";
      // 名单外的条目一律跳过：不执行、不回报＝不消费，原样留在服务端队列（拉取本身不受过滤影响）
      if (onlyTokens != null && !onlyTokens.contains(token)) continue;
      final capability = intent["capability"]?.toString() ?? "";
      final action = intent["action"]?.toString() ?? "";

      final reason = blockedReason(intent);
      if (reason == reasonDryRun) {
        results.add(_row(token, capability, statusDryRun, reasonDryRun, false, false, false, action: action));
        continue;
      }
      if (token.isEmpty) {
        results.add(
            _row(token, capability, statusBlocked, "missing_action_token", false, false, false, action: action));
        continue;
      }
      if (reason != null) {
        final reported = await _safeReport(doReport, token, false, reason);
        results.add(_row(token, capability, statusBlocked, reason, false, false, reported, action: action));
        continue;
      }

      final policy = await _resolvePolicy(doPolicy, capability);
      final onceEverConfirmed = await _resolveOnceEver(doOnceEver, capability);
      bool granted;
      try {
        granted = shouldConfirm(capability, policy,
                onceEverConfirmed: onceEverConfirmed, sessionConfirmed: confirmed)
            ? await confirm(capability)
            : true;
      } catch (_) {
        granted = false; // 确认通道异常＝没拿到同意
      }
      if (!granted) {
        results.add(_row(token, capability, statusConfirmDenied, "confirm_denied", false, false, false,
            action: action));
        continue;
      }
      confirmed.add(capability);
      if (policy == ActionConfirmPolicy.onceEver && !onceEverConfirmed) {
        await _safeMark(doMark, capability); // 落盘失败只是下次再问一次，不影响本次执行
      }

      bool ok;
      String detail;
      try {
        final r = await doExecute(intent);
        ok = r["ok"] == true;
        detail = (r["detail"] ?? r["message"] ?? "").toString();
        if (detail.trim().isEmpty) detail = ok ? "ok" : "failed";
      } catch (e) {
        ok = false;
        detail = "executor_error:$e";
      }
      detail = _clip(detail);
      final reported = await _safeReport(doReport, token, ok, detail);
      results.add(_row(token, capability, ok ? statusExecuted : statusFailed, detail, ok, true, reported,
          action: action));
    }
    return results;
  }

  /// 真机执行路径（默认 `execute`）：只复用既有能力，不新增 native。
  ///
  /// 前置不满足一律 `ok:false` + 明确 detail（让服务端熔断计数起作用），不猜、不装成功。
  static Future<Map<String, dynamic>> executeOnDevice(Map<String, dynamic> intent) async {
    if (!Platform.isAndroid) return _exec(false, "not_android");
    final capability = intent["capability"]?.toString() ?? "";
    switch (capability) {
      case capTap:
        return _runAccessibilityAction("click", intent["query"]?.toString() ?? "");
      case capSetText:
        return _runAccessibilityAction("set_text", intent["text"]?.toString() ?? "");
      case capOpenApp:
        return _openApp(intent["target_app"]?.toString() ?? "");
      default:
        return _exec(false, "$reasonUnsupported$capability");
    }
  }

  static Future<Map<String, dynamic>> _runAccessibilityAction(String action, String target) async {
    final health = await PhonePerceptionService.getServiceHealth();
    if (health["accessible"] != true) return _exec(false, "accessibility_off");
    if (!await PhonePerceptionService.isActionsEnabled()) return _exec(false, "actions_switch_off");
    if (target.trim().isEmpty) return _exec(false, "empty_target");
    final r = action == "set_text"
        ? await PhonePerceptionService.setTextOnFocus(target)
        : await PhonePerceptionService.performAction(action, target);
    final ok = r["ok"] == true;
    return _exec(ok, (r["message"] ?? (ok ? "ok" : "action_failed")).toString());
  }

  /// 启动应用：固定 monkey 模板，包名先过 [isSafePackageName]（不通过就不可能进 shell）。
  static Future<Map<String, dynamic>> _openApp(String packageName) async {
    if (!isSafePackageName(packageName)) return _exec(false, reasonUnsafeTarget);
    final st = await ShizukuService.status();
    if (st["serverRunning"] != true) return _exec(false, "shizuku_not_running");
    if (st["permissionGranted"] != true) return _exec(false, "shizuku_not_granted");
    final r = await ShizukuService.runShell("monkey -p $packageName 1");
    final ok = r["ok"] == true;
    return _exec(ok, ok ? "launched:$packageName" : "launch_failed:${r["stderr"] ?? ""}");
  }

  static Map<String, dynamic> _exec(bool ok, String detail) => {"ok": ok, "detail": detail};

  static Map<String, dynamic> _row(
    String token,
    String capability,
    String status,
    String detail,
    bool ok,
    bool executed,
    bool reported, {
    String action = "",
  }) =>
      {
        "action_token": token,
        "capability": capability,
        "action": action,
        "status": status,
        "ok": ok,
        "executed": executed,
        "reported": reported,
        "detail": detail,
      };

  static Future<bool> _safeReport(ActionReportFn report, String token, bool ok, String detail) async {
    try {
      return await report(token, ok, detail);
    } catch (_) {
      return false;
    }
  }

  /// 档位读不到就按**重档**（fail-closed：宁可多问一次，绝不静默放行）。
  static Future<ActionConfirmPolicy> _resolvePolicy(ActionPolicyFn policyOf, String capability) async {
    try {
      return await policyOf(capability);
    } catch (_) {
      return ActionConfirmPolicy.everyTime;
    }
  }

  /// 「是否已永久放行」读不到就当作没放行过（同样 fail-closed）。
  static Future<bool> _resolveOnceEver(ActionOnceEverCheckFn onceEverOf, String capability) async {
    try {
      return await onceEverOf(capability);
    } catch (_) {
      return false;
    }
  }

  static Future<void> _safeMark(ActionMarkOnceEverFn mark, String capability) async {
    try {
      await mark(capability);
    } catch (_) {
      // 标记失败只是下次再问一次
    }
  }

  static Future<bool> _serviceReport(String token, bool ok, String detail) =>
      DeviceActionService.reportResult(token, ok: ok, detail: detail);

  /// detail 进服务端审计（单行 key=value），过长会淹没日志，截一刀。
  static String _clip(String s) => s.length <= maxDetailLength ? s : s.substring(0, maxDetailLength);
}
