import "package:flutter/foundation.dart";

import "device_action_executor.dart";
import "device_action_prefs.dart";
import "device_action_service.dart";

/// X7-M4d：把「用户工作流的步骤序列」接到 M4a/M4b 的行动端口（内核裁决 + 审计 + 执行器）。
///
/// 分工严格切成两层：
/// - [planForWorkflow] / [inspectWorkflow]：**纯函数**，只做「整条能不能映射」的预检与载荷折算，
///   不碰网络、不碰 MethodChannel、不碰 prefs；
/// - [WorkflowActionBridge.runWorkflowSequence]：编排（读开关 → 预检 → 逐条「提交一条 + 只执行本条」 或 整条走旧路径），
///   开关、提交、执行、旧路径执行、日志**全部可注入**，因此单测无需任何外设。
///
/// 三条不可让步的口径（派单 P22 硬约束）：
/// ① 只有**整条**每一步都可映射、**且首步是 `launch_app`** 时才走新端口；任一步不可映射 → **整条**走旧路径
///    （不做半新半旧，也不做「能映射的走端口、其余偷偷本地做」）；
/// ② 端口**被拒 / 拿不到裁决 / 批准但取不到可执行条目** → **立即中止**并把机器可读 reason 原样带回结果，
///    **绝不回退旧路径**（回退＝绕过闸门）；
/// ③ 提交用**内置身份**，载荷只带该能力实际用到的字段，**不带 `plugin`**（后端不接受自称）。
///
/// 旧路径（`PhonePerceptionService._executeSingleStep` 无障碍 / Shizuku 双通道）行为、返回结构、
/// 日志文案逐字不变；本文件只在每行结果上追加 `via`（`"port"` / `"legacy"`）便于排查。

// ── 工作流步骤的字面量（与 `PhonePerceptionService` 既有 action 取值一致）──
const String wfActionLaunchApp = "launch_app";
const String wfActionClick = "click";
const String wfActionSetText = "set_text";

/// 不可映射原因（机器可读字面量，进日志也进 UI 可展示的 fallback 说明）。
class WorkflowPlanReject {
  static const String emptyWorkflow = "empty_workflow";
  static const String noLeadingLaunchApp = "no_leading_launch_app";
  static const String unsafeTargetApp = "unsafe_target_app";
  static const String missingTarget = "missing_target";
  static const String missingText = "missing_text";
  static const String malformedStep = "malformed_step";

  /// `unsupported_step:<action>`
  static const String unsupportedStepPrefix = "unsupported_step:";
  static String unsupportedStep(String action) => "$unsupportedStepPrefix$action";
}

/// 计划里的一条端口步骤：保留原始工作流动作/目标（回折结果要用），载荷是提交端口的字段。
class WorkflowPortStep {
  final int step; // 1-based，与工作流步骤同序
  final String action; // 原始 action 字面量（launch_app / click / set_text）
  final String target; // 原始 target（launch_app＝包名，click＝要点文本，set_text 一般为空）
  final Map<String, dynamic> payload;

  const WorkflowPortStep({
    required this.step,
    required this.action,
    required this.target,
    required this.payload,
  });

  String get capability => payload["capability"]?.toString() ?? "";
}

/// 一条「可整条映射」的工作流计划。
class WorkflowPortPlan {
  final String targetApp; // 首步 launch_app 的包名＝本条工作流的行动目标应用
  final List<WorkflowPortStep> steps;

  const WorkflowPortPlan({required this.targetApp, required this.steps});
}

/// 预检结果：`plan == null` 时 `reason` 给出**为什么**整条走旧路径（不只是 null）。
class WorkflowPlanResult {
  final WorkflowPortPlan? plan;
  final String reason; // 可映射时为空串

  const WorkflowPlanResult(this.plan, this.reason);

  bool get mappable => plan != null;
}

/// 纯预检 + 折算：整条可映射则出计划，否则 `plan` 为 null 且带机器可读原因。
///
/// `target_app` 在 M4a 契约里三条能力恒必填，而工作流步骤本身不带包名 ⇒ 只能取**首步 `launch_app`**，
/// 首步不是 `launch_app` 即整条不可映射（不是「那一步不可映射」）。
WorkflowPlanResult inspectWorkflow(List steps) {
  if (steps.isEmpty) return const WorkflowPlanResult(null, WorkflowPlanReject.emptyWorkflow);
  if (_actionOf(steps.first) != wfActionLaunchApp) {
    return const WorkflowPlanResult(null, WorkflowPlanReject.noLeadingLaunchApp);
  }

  final targetApp = _stringOf(steps.first, "target").trim();
  if (!DeviceActionExecutor.isSafePackageName(targetApp)) {
    return const WorkflowPlanResult(null, WorkflowPlanReject.unsafeTargetApp);
  }

  final portSteps = <WorkflowPortStep>[];
  for (var i = 0; i < steps.length; i++) {
    final step = steps[i];
    if (step is! Map) {
      return const WorkflowPlanResult(null, WorkflowPlanReject.malformedStep);
    }
    final action = step["action"]?.toString() ?? "";
    final target = step["target"]?.toString() ?? "";
    final Map<String, dynamic> payload;
    switch (action) {
      case wfActionLaunchApp:
        final pkg = target.trim();
        if (!DeviceActionExecutor.isSafePackageName(pkg)) {
          return const WorkflowPlanResult(null, WorkflowPlanReject.unsafeTargetApp);
        }
        payload = {
          "capability": DeviceActionExecutor.capOpenApp,
          "target_app": pkg,
        };
        break;
      case wfActionClick:
        if (target.trim().isEmpty) {
          return const WorkflowPlanResult(null, WorkflowPlanReject.missingTarget);
        }
        payload = {
          "capability": DeviceActionExecutor.capTap,
          "target_app": targetApp,
          "by": "text",
          "query": target,
        };
        break;
      case wfActionSetText:
        final text = step["text"]?.toString() ?? "";
        if (text.trim().isEmpty) {
          return const WorkflowPlanResult(null, WorkflowPlanReject.missingText);
        }
        payload = {
          "capability": DeviceActionExecutor.capSetText,
          "target_app": targetApp,
          "text": text,
        };
        break;
      default:
        return WorkflowPlanResult(null, WorkflowPlanReject.unsupportedStep(action));
    }
    portSteps.add(
        WorkflowPortStep(step: i + 1, action: action, target: target, payload: payload));
  }
  return WorkflowPlanResult(
      WorkflowPortPlan(targetApp: targetApp, steps: portSteps), "");
}

/// 派单签名：可映射则返回计划，否则 null（＝整条走旧路径）。要原因请用 [inspectWorkflow]。
WorkflowPortPlan? planForWorkflow(List steps) => inspectWorkflow(steps).plan;

/// 感知页「工作流端口自检」（M4d-3）用的目标应用：自家 app，包名合法且一定装着。
const String kWorkflowSelfCheckTargetApp = "com.gituu.ambrace.ai_companion";

/// 构造**最小可映射工作流**：首步 `launch_app` 打开自家 app，第二步 `click` 一个传进来的真实可见文本。
/// 纯函数（不执行任何东西），因此「自检序列是否满足映射条件」本身可被单测钉住。
List<Map<String, dynamic>> workflowSelfCheckSteps(String clickTarget) => [
      {"action": wfActionLaunchApp, "target": kWorkflowSelfCheckTargetApp},
      {"action": wfActionClick, "target": clickTarget},
    ];

String _actionOf(Object? step) {
  if (step is! Map) return "";
  return step["action"]?.toString() ?? "";
}

String _stringOf(Object? step, String key) {
  if (step is! Map) return "";
  return step[key]?.toString() ?? "";
}

/// 旧路径单步执行的注入位（真机＝ `PhonePerceptionService._executeSingleStep`）。
typedef LegacyStepExecutor = Future<Map<String, dynamic>> Function(Map step, int stepNo);
typedef WorkflowBridgeEnabled = Future<bool> Function();
typedef WorkflowIntentSubmitter = Future<ActionSubmitResult> Function(Map<String, dynamic> payload);
typedef WorkflowPendingRunner = Future<List<Map<String, dynamic>>> Function(
    Set<String> confirmedTypes);

/// 带名单的执行侧注入位：`onlyTokens` 之外的待办**不执行也不回报**（留在服务端队列）。
/// 留着 [WorkflowPendingRunner] 给不区分条目的调用方（拿到整条队列台账）；两者同时注入时以本注入位为准。
typedef WorkflowScopedPendingRunner = Future<List<Map<String, dynamic>>> Function(
    Set<String> confirmedTypes, Set<String> onlyTokens);
typedef WorkflowInfoLog = void Function(String message);

class WorkflowActionBridge {
  static const String viaPort = "port";
  static const String viaLegacy = "legacy";

  /// 中档（每类本次会话首次确认）的会话内已确认集合——桥的生命周期，跨多次工作流复用。
  static final Set<String> sessionConfirmed = <String>{};

  /// 确认弹窗注入位（执行侧档位仍由 `DeviceActionExecutor.runOnce` 按 prefs 判定）。
  /// **未注入＝拿不到同意**（fail-closed：需要确认的那一条按 `confirm_denied` 中止，不静默放行）。
  static ActionConfirmFn? confirmHandler;

  /// 「运行前设、运行后清」的唯一入口：跑 [body]（含异常/提前 return）期间 handler 生效，结束必摘回原值。
  /// 不摘＝一个捕获着调用方 BuildContext 的闭包长期挂在这里：页面已卸载还会弹框、别的入口更会拿旧页面弹窗放行。
  static Future<T> withConfirmHandler<T>(ActionConfirmFn handler, Future<T> Function() body) async {
    final previous = confirmHandler;
    confirmHandler = handler;
    try {
      return await body();
    } finally {
      confirmHandler = previous;
    }
  }

  /// 工作流执行总入口：开关关 / 整条不可映射 → 旧路径；否则逐条走端口（每条只执行自己那一条）。
  static Future<List<Map<String, dynamic>>> runWorkflowSequence({
    required List<Map> steps,
    required LegacyStepExecutor legacyStep,
    WorkflowBridgeEnabled? bridgeEnabled,
    WorkflowIntentSubmitter? submit,
    WorkflowPendingRunner? runner,
    WorkflowScopedPendingRunner? scopedRunner,
    WorkflowInfoLog? log,
  }) async {
    final info = log ?? _defaultLog;
    if (!await _bridgeOn(bridgeEnabled ?? DeviceActionPrefs.isWorkflowBridgeEnabled)) {
      return _runLegacy(steps, legacyStep);
    }
    final inspected = inspectWorkflow(steps);
    final plan = inspected.plan;
    if (plan == null) {
      info("fallback_legacy reason=${inspected.reason} steps=${steps.length}");
      return _runLegacy(steps, legacyStep);
    }
    info("via_port target_app=${plan.targetApp} steps=${plan.steps.length}");
    return _runPort(
      plan: plan,
      submit: submit ?? _defaultSubmit,
      runScoped: _resolveRunner(scopedRunner, runner),
      log: info,
    );
  }

  /// 执行侧优先级：带名单的注入位 > 旧注入位（不区分条目）> 真机默认（只跑本条）。
  static WorkflowScopedPendingRunner _resolveRunner(
    WorkflowScopedPendingRunner? scoped,
    WorkflowPendingRunner? legacy,
  ) {
    if (scoped != null) return scoped;
    if (legacy != null) return (confirmed, _) => legacy(confirmed);
    return _defaultScopedRunner;
  }

  /// 开关读失败＝关（＝旧路径），与 prefs 缺省口径一致。
  static Future<bool> _bridgeOn(WorkflowBridgeEnabled read) async {
    try {
      return await read();
    } catch (_) {
      return false;
    }
  }

  /// 旧路径逐字等价：逐步执行、任一步失败立即停止、返回同序结果（只多一个 `via` 标记）。
  static Future<List<Map<String, dynamic>>> _runLegacy(
    List<Map> steps,
    LegacyStepExecutor legacyStep,
  ) async {
    final results = <Map<String, dynamic>>[];
    for (var i = 0; i < steps.length; i++) {
      final r = await legacyStep(steps[i], i + 1);
      results.add(Map<String, dynamic>.from(r)..["via"] = viaLegacy);
      if (!(r["ok"] as bool? ?? false)) break;
    }
    return results;
  }

  static Future<List<Map<String, dynamic>>> _runPort({
    required WorkflowPortPlan plan,
    required WorkflowIntentSubmitter submit,
    required WorkflowScopedPendingRunner runScoped,
    required WorkflowInfoLog log,
  }) async {
    final results = <Map<String, dynamic>>[];
    for (final ps in plan.steps) {
      final ActionSubmitResult sub;
      try {
        sub = await submit(ps.payload);
      } catch (e) {
        // 提交通道异常＝没拿到裁决：中止并如实带回，**不回退旧路径**
        results.add(_portRow(ps, ok: false, message: "port_submit_failed:$e", status: "submit_failed"));
        log("abort step=${ps.step} reason=port_submit_failed");
        return results;
      }
      if (!sub.allowed) {
        results.add(_portRow(ps, ok: false, message: sub.reason, status: "rejected"));
        log("abort step=${ps.step} reason=${sub.reason}");
        return results;
      }
      if (sub.actionToken.isEmpty) {
        final reason =
            sub.dryRun || sub.status == "dry_run" ? "dry_run_skipped" : "missing_action_token";
        results.add(_portRow(ps, ok: false, message: reason, status: "no_token"));
        log("abort step=${ps.step} reason=$reason");
        return results;
      }

      final List<Map<String, dynamic>> rows;
      try {
        // 提交一条 → 只执行这一条：名单外的待办（含该账号其它已批准条目）不执行、不回报、留在服务端队列
        rows = await runScoped(sessionConfirmed, {sub.actionToken});
      } catch (e) {
        results.add(_portRow(ps, ok: false, message: "run_once_failed:$e", status: "runner_failed"));
        log("abort step=${ps.step} reason=run_once_failed");
        return results;
      }
      final row = _rowForToken(rows, sub.actionToken);
      if (row == null) {
        // 批准了却没取回该条目的执行结论＝不能假定已执行：中止（**不回退旧路径**）。
        // 只有「整批没取到」（fetch_failed）才借用那条 detail，其余一律如实报 intent_not_executed。
        final fetchFailed = rows
            .where((r) => (r["status"] ?? "").toString() == DeviceActionExecutor.statusFetchFailed)
            .toList();
        final borrowed = (fetchFailed.isNotEmpty ? fetchFailed.first["detail"] : null)?.toString() ?? "";
        final reason = borrowed.isNotEmpty ? borrowed : "intent_not_executed";
        results.add(_portRow(ps,
            ok: false,
            message: reason,
            status: "not_executed",
            token: sub.actionToken,
            capability: ps.capability));
        log("abort step=${ps.step} reason=$reason");
        return results;
      }
      final ok = row["ok"] == true;
      final rowCap = (row["capability"] ?? "").toString();
      results.add(_portRow(
        ps,
        ok: ok,
        message: (row["detail"] ?? "").toString(),
        status: (row["status"] ?? "").toString(),
        token: sub.actionToken,
        capability: rowCap.isEmpty ? ps.capability : rowCap,
      ));
      log("done step=${ps.step} ok=$ok status=${row["status"]}");
      if (!ok) return results; // 任一步失败立即停止（与旧路径一致）
    }
    return results;
  }

  /// 从 runOnce 台账里按 action_token 找回本条的执行结论。
  static Map<String, dynamic>? _rowForToken(List<Map<String, dynamic>> rows, String token) {
    for (final r in rows) {
      if ((r["action_token"] ?? "").toString() == token) return r;
    }
    return null;
  }

  /// 折成与旧路径**同构**的结果项（step/action/target/ok/message），另加端口侧排查字段。
  static Map<String, dynamic> _portRow(
    WorkflowPortStep ps, {
    required bool ok,
    required String message,
    required String status,
    String token = "",
    String capability = "",
  }) =>
      {
        "step": ps.step,
        "action": ps.action,
        "target": ps.target,
        "ok": ok,
        "message": message,
        "via": viaPort,
        "capability": capability.isEmpty ? ps.capability : capability,
        "port_status": status,
        "action_token": token,
      };

  // ── 真机默认实现（单测一律注入假件，不走这里）──
  static Future<ActionSubmitResult> _defaultSubmit(Map<String, dynamic> payload) =>
      DeviceActionService.submitIntent(
        capability: payload["capability"]?.toString() ?? "",
        targetApp: payload["target_app"]?.toString() ?? "",
        by: payload["by"] as String?,
        query: payload["query"] as String?,
        text: payload["text"] as String?,
      ); // 内置身份由后端端点固定：载荷里没有 plugin 的位置

  static Future<List<Map<String, dynamic>>> _defaultScopedRunner(
    Set<String> confirmedTypes,
    Set<String> onlyTokens,
  ) =>
      DeviceActionExecutor.runOnce(
        confirmedTypes: confirmedTypes,
        onlyTokens: onlyTokens,
        confirm: confirmHandler ?? _headlessConfirm,
      );

  static Future<bool> _headlessConfirm(String capability) async {
    _defaultLog("confirm_unavailable capability=$capability");
    return false;
  }

  static void _defaultLog(String message) => debugPrint("[workflow_bridge][INFO] $message");
}
