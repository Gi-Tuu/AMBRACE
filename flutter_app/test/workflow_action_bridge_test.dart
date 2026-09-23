import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";
import "package:ai_companion/services/device_action_executor.dart";
import "package:ai_companion/services/device_action_prefs.dart";
import "package:ai_companion/services/device_action_service.dart";
import "package:ai_companion/services/workflow_action_bridge.dart";

/// X7-M4d 工作流→行动端口桥单测：
/// 纯预检（planForWorkflow / inspectWorkflow）+ 编排（runWorkflowSequence）全部走注入假件，
/// **不碰网络、不碰 MethodChannel、不碰真机执行**。
/// 三条硬约束逐条钉住：①只在整条可映射且首步 launch_app 时才走端口；②端口被拒立即中止且**不回退旧路径**；
/// ③提交载荷**不带 plugin 字段**。

Map<String, dynamic> _launch([String pkg = "com.example.app"]) =>
    {"action": "launch_app", "target": pkg};
Map<String, dynamic> _click(String text) => {"action": "click", "target": text};
Map<String, dynamic> _setText(String text) => {"action": "set_text", "text": text};
Map<String, dynamic> _raw(String action) => {"action": action, "target": "x"};

/// 端口/旧路径假件：谁被调用、调用顺序、提交载荷全部记账。
class _FakePort {
  final List<Map<String, dynamic>> submitted = [];
  final List<String> logs = [];
  final List<Set<String>> confirmedSeen = [];
  int runnerCalls = 0;
  int legacyCalls = 0;
  bool legacyOk = true;
  ActionSubmitResult Function(int callIndex, Map<String, dynamic> payload)? verdict;
  List<Map<String, dynamic>> Function(int callIndex, String token)? rowsFor;

  /// 旧路径假件：返回项与 `_executeSingleStep` 同构
  LegacyStepExecutor get legacyStep => (step, stepNo) async {
        legacyCalls++;
        return {
          "step": stepNo,
          "action": step["action"] ?? "",
          "target": step["target"] ?? "",
          "ok": legacyOk,
          "message": "legacy:$stepNo",
        };
      };

  WorkflowIntentSubmitter get submit => (payload) async {
        submitted.add(Map<String, dynamic>.from(payload));
        final i = submitted.length;
        return verdict?.call(i, Map<String, dynamic>.from(payload)) ??
            ActionSubmitResult(allowed: true, status: "approved", actionToken: "tk-$i");
      };

  WorkflowPendingRunner get runner => (confirmed) async {
        runnerCalls++;
        confirmedSeen.add(Set<String>.from(confirmed));
        return rowsFor?.call(runnerCalls, "tk-$runnerCalls") ??
            [
              {
                "action_token": "tk-$runnerCalls",
                "capability": submitted[runnerCalls - 1]["capability"] ?? "",
                "ok": true,
                "status": DeviceActionExecutor.statusExecuted,
                "detail": "done:$runnerCalls",
              }
            ];
      };

  Future<List<Map<String, dynamic>>> run(
    List<Map> steps, {
    bool enabled = true,
    Future<bool> Function()? bridgeEnabled,
  }) =>
      WorkflowActionBridge.runWorkflowSequence(
        steps: steps,
        legacyStep: legacyStep,
        bridgeEnabled: bridgeEnabled ?? (() async => enabled),
        submit: submit,
        runner: runner,
        log: logs.add,
      );
}

void main() {
  setUp(() {
    WorkflowActionBridge.sessionConfirmed.clear();
    WorkflowActionBridge.confirmHandler = null;
  });

  group("整条预检：可映射计划", () {
    test("①launch_app + click + set_text → 计划正确且 target_app 取自首步", () {
      final plan = planForWorkflow([
        _launch("com.demo.target"),
        _click("搜索"),
        _setText("你好世界"),
      ]);
      expect(plan, isNotNull);
      expect(plan!.targetApp, "com.demo.target");
      expect(plan.steps.length, 3);
      expect(plan.steps[0].capability, DeviceActionExecutor.capOpenApp);
      expect(plan.steps[0].payload, {
        "capability": "action_open_app",
        "target_app": "com.demo.target",
      });
      expect(plan.steps[1].capability, DeviceActionExecutor.capTap);
      expect(plan.steps[1].payload, {
        "capability": "action_tap",
        "target_app": "com.demo.target", // 首步包名，不是本步 target
        "by": "text",
        "query": "搜索",
      });
      expect(plan.steps[2].payload, {
        "capability": "action_set_text",
        "target_app": "com.demo.target",
        "text": "你好世界",
      });
      expect(plan.steps.map((s) => s.step).toList(), [1, 2, 3]);
      expect(plan.steps.map((s) => s.action).toList(), ["launch_app", "click", "set_text"]);
    });

    test("①b多条 click 恒取首步包名；中间再次 launch_app 用自己的包名", () {
      final plan = planForWorkflow([
        _launch("com.first.app"),
        _click("A"),
        _launch("com.second.app"),
        _click("B"),
      ])!;
      expect(plan.steps[0].payload["target_app"], "com.first.app");
      expect(plan.steps[1].payload["target_app"], "com.first.app");
      expect(plan.steps[2].payload["target_app"], "com.second.app");
      expect(plan.steps[3].payload["target_app"], "com.first.app");
      expect(plan.targetApp, "com.first.app");
    });
  });

  group("整条预检：不可映射（返回 null 且给出原因）", () {
    test("②首步不是 launch_app", () {
      final r = inspectWorkflow([_click("搜索"), _launch()]);
      expect(r.mappable, isFalse);
      expect(r.reason, WorkflowPlanReject.noLeadingLaunchApp);
      expect(planForWorkflow([_click("搜索"), _launch()]), isNull);
    });

    test("③含 tap_xy → 整条不可映射", () {
      final r = inspectWorkflow([_launch(), _click("A"), _raw("tap_xy")]);
      expect(r.plan, isNull);
      expect(r.reason, "${WorkflowPlanReject.unsupportedStepPrefix}tap_xy");
    });

    test("③含 swipe → 整条不可映射", () {
      final r = inspectWorkflow([_launch(), _raw("swipe")]);
      expect(r.plan, isNull);
      expect(r.reason, "${WorkflowPlanReject.unsupportedStepPrefix}swipe");
    });

    test("③含 back → 整条不可映射", () {
      final r = inspectWorkflow([_launch(), _raw("back")]);
      expect(r.plan, isNull);
      expect(r.reason, "${WorkflowPlanReject.unsupportedStepPrefix}back");
    });

    test("③含 wait → 整条不可映射", () {
      final r = inspectWorkflow([_launch(), {"action": "wait", "ms": 800}]);
      expect(r.plan, isNull);
      expect(r.reason, "${WorkflowPlanReject.unsupportedStepPrefix}wait");
    });

    test("③其余本机动作（go_home / longclick / scroll / 空 action）同样整条不可映射", () {
      for (final a in ["go_home", "longclick", "scroll", "back", ""]) {
        final r = inspectWorkflow([_launch(), _raw(a)]);
        expect(r.plan, isNull, reason: "action=$a 不应被映射");
        expect(r.reason, "${WorkflowPlanReject.unsupportedStepPrefix}$a");
      }
    });

    test("④空工作流 → 不可映射", () {
      final r = inspectWorkflow([]);
      expect(r.plan, isNull);
      expect(r.reason, WorkflowPlanReject.emptyWorkflow);
    });

    test("⑤首步 launch_app 包名含空格 → unsafe_target_app", () {
      final r = inspectWorkflow([_launch("com.a b"), _click("A")]);
      expect(r.plan, isNull);
      expect(r.reason, WorkflowPlanReject.unsafeTargetApp);
    });

    test("⑤首步包名为空 → unsafe_target_app（拿不到 target_app 就无法提交意图）", () {
      final r = inspectWorkflow([_launch(""), _click("A")]);
      expect(r.plan, isNull);
      expect(r.reason, WorkflowPlanReject.unsafeTargetApp);
      expect(inspectWorkflow([_launch("com")]).reason, WorkflowPlanReject.unsafeTargetApp);
    });

    test("⑤中途 launch_app 包名非法 → 整条不可映射（不做半新半旧）", () {
      final r = inspectWorkflow([_launch("com.demo.app"), _click("A"), _launch("bad pkg")]);
      expect(r.plan, isNull);
      expect(r.reason, WorkflowPlanReject.unsafeTargetApp);
    });

    test("⑥click 缺 target → 不可映射", () {
      expect(inspectWorkflow([_launch(), {"action": "click"}]).reason, WorkflowPlanReject.missingTarget);
      expect(inspectWorkflow([_launch(), _click("   ")]).reason, WorkflowPlanReject.missingTarget);
    });

    test("⑥bset_text 缺 text → 不可映射（不发注定被闸门拒的空意图，也不猜内容）", () {
      expect(inspectWorkflow([_launch(), {"action": "set_text"}]).reason, WorkflowPlanReject.missingText);
      expect(inspectWorkflow([_launch(), _setText("  ")]).reason, WorkflowPlanReject.missingText);
    });

    test("不可映射原因的机器可读字面量稳定（日志/UI 依赖）", () {
      expect(WorkflowPlanReject.emptyWorkflow, "empty_workflow");
      expect(WorkflowPlanReject.noLeadingLaunchApp, "no_leading_launch_app");
      expect(WorkflowPlanReject.unsafeTargetApp, "unsafe_target_app");
      expect(WorkflowPlanReject.missingTarget, "missing_target");
      expect(WorkflowPlanReject.missingText, "missing_text");
      expect(WorkflowPlanReject.malformedStep, "malformed_step");
      expect(WorkflowPlanReject.unsupportedStep("tap_xy"), "unsupported_step:tap_xy");
    });
  });

  group("开关（客户端 prefs，缺省＝关）", () {
    test("⑦缺省（prefs 里没这个键）＝关", () async {
      SharedPreferences.setMockInitialValues({});
      expect(await DeviceActionPrefs.isWorkflowBridgeEnabled(), isFalse);
    });

    test("⑦b写入开 / 关可往返；键名与派单口径一致", () async {
      SharedPreferences.setMockInitialValues({});
      expect(DeviceActionPrefs.workflowBridgeKey, "device_action_workflow_bridge");
      expect(await DeviceActionPrefs.setWorkflowBridgeEnabled(true), isTrue);
      expect(await DeviceActionPrefs.isWorkflowBridgeEnabled(), isTrue);
      expect(await DeviceActionPrefs.setWorkflowBridgeEnabled(false), isTrue);
      expect(await DeviceActionPrefs.isWorkflowBridgeEnabled(), isFalse);
    });

    test("⑦c开关缺省＝关：即使整条可映射也走旧路径，且一次端口提交都没发生", () async {
      SharedPreferences.setMockInitialValues({});
      final fake = _FakePort();
      final r = await WorkflowActionBridge.runWorkflowSequence(
        steps: [_launch(), _click("A")],
        legacyStep: fake.legacyStep,
        submit: fake.submit,
        runner: fake.runner,
        log: fake.logs.add,
      );
      expect(await DeviceActionPrefs.isWorkflowBridgeEnabled(), isFalse, reason: "缺省必须是关");
      expect(fake.submitted, isEmpty);
      expect(fake.runnerCalls, 0);
      expect(fake.legacyCalls, 2);
      expect(r.every((row) => row["via"] == WorkflowActionBridge.viaLegacy), isTrue);
    });

    test("开关读取抛异常 → 按关处理（走旧路径，不碰端口）", () async {
      final fake = _FakePort();
      await fake.run([_launch(), _click("A")], bridgeEnabled: () async => throw StateError("prefs"));
      expect(fake.submitted, isEmpty);
      expect(fake.legacyCalls, 2);
    });
  });

  group("编排：开关开但整条不可映射 → 整条旧路径", () {
    test("⑧计划为 null → 旧执行器被调用且端口提交一次都没发生，并记原因", () async {
      final fake = _FakePort();
      final r = await fake.run([_launch("com.demo.app"), _click("A"), _raw("tap_xy")]);
      expect(fake.submitted, isEmpty);
      expect(fake.runnerCalls, 0);
      expect(fake.legacyCalls, 3, reason: "整条走旧路径（不做半新半旧）");
      expect(r.map((row) => row["via"]).toList(), [
        WorkflowActionBridge.viaLegacy,
        WorkflowActionBridge.viaLegacy,
        WorkflowActionBridge.viaLegacy,
      ]);
      expect(fake.logs.any((l) => l.contains("fallback_legacy") && l.contains("unsupported_step:tap_xy")),
          isTrue,
          reason: "回落旧路径必须给出原因");
    });

    test("⑧b旧路径任一步失败立即停止（与旧行为一致）", () async {
      final fake = _FakePort()..legacyOk = false;
      final r = await fake.run([_launch("com.demo.app"), _click("A"), _click("B")], enabled: false);
      expect(fake.legacyCalls, 1);
      expect(r.length, 1);
      expect(r.single["ok"], isFalse);
      expect(r.single["message"], "legacy:1");
    });
  });

  group("编排：开关开且整条可映射 → 逐条走端口", () {
    test("⑨依次提交并执行、结果与步骤同序、载荷里没有 plugin 字段", () async {
      final fake = _FakePort();
      final r = await fake.run([_launch("com.demo.target"), _click("发送"), _setText("你好")]);
      expect(fake.submitted.length, 3);
      expect(fake.runnerCalls, 3);
      expect(fake.legacyCalls, 0, reason: "走端口就不该再碰旧执行器");
      expect(fake.submitted.map((p) => p["capability"]).toList(),
          ["action_open_app", "action_tap", "action_set_text"]);
      for (final p in fake.submitted) {
        expect(p.containsKey("plugin"), isFalse, reason: "内置身份由端点固定，载荷不得自称");
        expect(p["target_app"], "com.demo.target");
      }
      expect(fake.submitted[1], {"capability": "action_tap", "target_app": "com.demo.target", "by": "text", "query": "发送"});
      expect(r.length, 3);
      expect(r.map((row) => row["step"]).toList(), [1, 2, 3]);
      expect(r.map((row) => row["action"]).toList(), ["launch_app", "click", "set_text"]);
      expect(r.map((row) => row["target"]).toList(), ["com.demo.target", "发送", ""]);
      expect(r.every((row) => row["ok"] == true), isTrue);
      expect(r.every((row) => row["via"] == WorkflowActionBridge.viaPort), isTrue);
      expect(r.map((row) => row["message"]).toList(), ["done:1", "done:2", "done:3"]);
    });

    test("⑩端口被拒 → 立即中止、旧执行器一次都没被调用、reason 原样带回", () async {
      final fake = _FakePort()
        ..verdict = (i, _) => i == 2
            ? const ActionSubmitResult(allowed: false, reason: "target_not_allowed")
            : ActionSubmitResult(allowed: true, status: "approved", actionToken: "tk-$i");
      final r = await fake.run([_launch("com.demo.app"), _click("发送"), _setText("你好")]);
      expect(fake.submitted.length, 2, reason: "第 2 步被拒后不得再提交第 3 步");
      expect(fake.runnerCalls, 1, reason: "被拒的那条不得去取待办");
      expect(fake.legacyCalls, 0, reason: "端口被拒绝不偷偷回退旧路径（＝绕过闸门）");
      expect(r.length, 2);
      expect(r[0]["ok"], isTrue);
      expect(r[1]["ok"], isFalse);
      expect(r[1]["message"], "target_not_allowed", reason: "服务端 reason 原样带回，不加解释性包装");
      expect(r[1]["port_status"], "rejected");
      expect(r[1]["step"], 2);
      expect(fake.logs.any((l) => l.contains("abort step=2") && l.contains("target_not_allowed")), isTrue);
    });

    test("⑩b第一步就被拒 → 结果为单行失败且完全没有本机执行", () async {
      final fake = _FakePort()
        ..verdict = (_, __) => const ActionSubmitResult(allowed: false, reason: "actions_disabled");
      final r = await fake.run([_launch("com.demo.app"), _click("发送")]);
      expect(fake.legacyCalls, 0);
      expect(fake.runnerCalls, 0);
      expect(r.single["ok"], isFalse);
      expect(r.single["message"], "actions_disabled");
      expect(r.single["via"], WorkflowActionBridge.viaPort);
    });

    test("⑩c提交通道抛异常（拿不到裁决）→ 中止且不回退旧路径", () async {
      final fake = _FakePort()
        ..verdict = (i, _) => throw StateError("boom-$i");
      final r = await fake.run([_launch("com.demo.app"), _click("发送")]);
      expect(fake.legacyCalls, 0);
      expect(fake.runnerCalls, 0);
      expect(r.single["ok"], isFalse);
      expect(r.single["message"], contains("port_submit_failed"));
    });

    test("⑩d批准但没有 token（干跑裁决）→ 如实失败并停止，不去执行也不回退", () async {
      final fake = _FakePort()
        ..verdict = (_, __) => const ActionSubmitResult(allowed: true, status: "dry_run", dryRun: true);
      final r = await fake.run([_launch("com.demo.app"), _click("发送")]);
      expect(fake.runnerCalls, 0);
      expect(fake.legacyCalls, 0);
      expect(r.single["ok"], isFalse);
      expect(r.single["message"], "dry_run_skipped");
    });

    test("⑩e批准但待办台账里取不到该 token → 不能假定已执行，中止", () async {
      final fake = _FakePort()..rowsFor = (_, __) => [
            {"action_token": "other", "capability": "action_open_app", "ok": true, "status": "executed_ok", "detail": "x"}
          ];
      final r = await fake.run([_launch("com.demo.app"), _click("发送")]);
      expect(fake.runnerCalls, 1);
      expect(fake.legacyCalls, 0);
      expect(r.single["ok"], isFalse);
      expect(r.single["message"], "intent_not_executed");
      expect(r.single["action_token"], "tk-1");
    });

    test("⑩f执行台账报失败（如 confirm_denied）→ 停止后续步骤", () async {
      final fake = _FakePort()..rowsFor = (call, token) => [
            {
              "action_token": token,
              "capability": "action_tap",
              "ok": call != 2,
              "status": call == 2 ? DeviceActionExecutor.statusConfirmDenied : DeviceActionExecutor.statusExecuted,
              "detail": call == 2 ? "confirm_denied" : "ok",
            }
          ];
      final r = await fake.run([_launch("com.demo.app"), _click("发送"), _setText("你好")]);
      expect(fake.submitted.length, 2, reason: "第 2 步没执行成功就不提交第 3 步");
      expect(r.length, 2);
      expect(r[0]["ok"], isTrue);
      expect(r[1]["ok"], isFalse);
      expect(r[1]["message"], "confirm_denied");
      expect(r[1]["capability"], "action_tap");
    });

    test("会话内已确认集合透传给执行侧（中档「每类本次会话首次」的记账位置在桥上）", () async {
      final fake = _FakePort();
      await fake.run([_launch("com.demo.app"), _click("发送")]);
      expect(fake.confirmedSeen.length, 2);
      expect(fake.confirmedSeen.first, isEmpty);
    });

    test("⑪set_text 的文本原样透传到意图载荷（不 trim、不翻译）", () async {
      final fake = _FakePort();
      final raw = "  你好 世界  https://example.com/a b  ";
      await fake.run([_launch("com.demo.app"), _setText(raw)]);
      expect(fake.submitted[1]["text"], raw);
      expect(fake.submitted[1]["capability"], DeviceActionExecutor.capSetText);
    });

    test("⑫端口结果项与旧路径同构（step/action/target/ok/message 全在）", () async {
      final portFake = _FakePort();
      final portRows = await portFake.run([_launch("com.demo.app"), _click("发送")]);
      final legacyFake = _FakePort();
      final legacyRows = await legacyFake.run([_launch("com.demo.app"), _click("发送")], enabled: false);
      const legacyKeys = {"step", "action", "target", "ok", "message"};
      for (final row in [...portRows, ...legacyRows]) {
        for (final k in legacyKeys) {
          expect(row.containsKey(k), isTrue, reason: "$k 缺失：$row");
        }
        expect(row["step"], isA<int>());
        expect(row["ok"], isA<bool>());
        expect(row["message"], isA<String>());
      }
      expect(legacyRows.first["via"], WorkflowActionBridge.viaLegacy);
      expect(legacyRows.first.keys.toSet().difference(legacyKeys), {"via"});
      expect(portRows.first["via"], WorkflowActionBridge.viaPort);
      expect(
          portRows.first.keys.toSet().difference(legacyKeys),
          {
            "via",
            "capability",
            "port_status",
            "action_token",
          });
    });
  });

  // ── M4d-2 追加：确认弹窗注入位 +「提交一条 → 只执行本条」（既有 31 例一字未动）──
  group("M4d-2：确认注入位与只执行本条", () {
    // 服务端队列里除了本条工作流的三步，还有**该账号另一条**已批准待办（tk-other）：
    // 「只执行本条」的验收点就是它一次都不该被碰。
    const pendingQueue = <Map<String, dynamic>>[
      {
        "action_token": "tk-1",
        "capability": "action_open_app",
        "action": "open_app",
        "target_app": "com.demo.app",
        "dry_run": false,
      },
      {
        "action_token": "tk-2",
        "capability": "action_tap",
        "action": "tap",
        "target_app": "com.demo.app",
        "by": "text",
        "query": "发送",
        "dry_run": false,
      },
      {
        "action_token": "tk-3",
        "capability": "action_set_text",
        "action": "set_text",
        "target_app": "com.demo.app",
        "text": "你好",
        "dry_run": false,
      },
      {
        "action_token": "tk-other",
        "capability": "action_open_app",
        "action": "open_app",
        "target_app": "com.other.app",
        "dry_run": false,
      },
    ];

    /// 执行侧走**真实** `DeviceActionExecutor.runOnce`（只把网络与真机执行换成假件）：
    /// 这样钉住的是「桥 → 执行器 → 确认」整条 fail-closed 链路，而不是桥自己的记账。
    WorkflowScopedPendingRunner scopedRunner({
      required List<String> executed,
      required List<String> reported,
      required List<List<String>> scopes,
    }) =>
        (confirmed, only) async {
          scopes.add(only.toList());
          return DeviceActionExecutor.runOnce(
            confirmedTypes: confirmed,
            onlyTokens: only,
            confirm: (capability) async {
              final h = WorkflowActionBridge.confirmHandler;
              // 没装 handler＝拿不到同意（与桥真机默认实现的 _headlessConfirm 同口径）
              return h == null ? false : await h(capability);
            },
            execute: (intent) async {
              executed.add(intent["action_token"]?.toString() ?? "");
              return {"ok": true, "detail": "done"};
            },
            fetchPending: () async => PendingActions(pendingQueue),
            report: (token, ok, detail) async {
              reported.add(token);
              return true;
            },
            policyOf: (capability) async => ActionConfirmPolicy.firstPerType,
            onceEverConfirmedOf: (capability) async => false,
            markOnceEverConfirmed: (capability) async => true,
          );
        };

    Future<List<Map<String, dynamic>>> runSteps(_FakePort fake, WorkflowScopedPendingRunner runner,
            List<Map> steps) =>
        WorkflowActionBridge.runWorkflowSequence(
          steps: steps,
          legacyStep: fake.legacyStep,
          bridgeEnabled: () async => true,
          submit: fake.submit,
          scopedRunner: runner,
          log: fake.logs.add,
        );

    test("④逐条推进：每步恰好一次提交 + 一次 runOnce，名单只含本条 token", () async {
      final fake = _FakePort();
      final executed = <String>[];
      final reported = <String>[];
      final scopes = <List<String>>[];
      WorkflowActionBridge.confirmHandler = (capability) async => true;
      final r = await runSteps(
          fake,
          scopedRunner(executed: executed, reported: reported, scopes: scopes),
          [_launch("com.demo.app"), _click("发送"), _setText("你好")]);
      expect(fake.submitted.length, 3);
      expect(scopes, [["tk-1"], ["tk-2"], ["tk-3"]], reason: "提交一条就只跑这一条，绝不顺带跑全队列");
      expect(executed, ["tk-1", "tk-2", "tk-3"]);
      expect(reported, ["tk-1", "tk-2", "tk-3"]);
      expect(r.map((row) => row["ok"]).toList(), [true, true, true]);
      expect(r.map((row) => row["action_token"]).toList(), ["tk-1", "tk-2", "tk-3"]);
      expect(fake.legacyCalls, 0);
    });

    test("⑤handler 拿不到 context（页面已卸载＝返回 false）→ 第一条 confirm_denied、零执行零回报、不回退", () async {
      final fake = _FakePort();
      final executed = <String>[];
      final reported = <String>[];
      final scopes = <List<String>>[];
      WorkflowActionBridge.confirmHandler = (capability) async => false; // 真实实现里 !mounted 就长这样
      final r = await runSteps(
          fake,
          scopedRunner(executed: executed, reported: reported, scopes: scopes),
          [_launch("com.demo.app"), _click("发送")]);
      expect(scopes, [["tk-1"]]);
      expect(fake.submitted.length, 1, reason: "第一条就被拒 → 不再提交第二条");
      expect(executed, isEmpty);
      expect(reported, isEmpty, reason: "拒绝确认不回报");
      expect(r.single["ok"], isFalse);
      expect(r.single["port_status"], DeviceActionExecutor.statusConfirmDenied);
      expect(r.single["message"], "confirm_denied");
      expect(r.single["via"], WorkflowActionBridge.viaPort);
      expect(fake.legacyCalls, 0, reason: "被拒绝不回退旧执行器");
    });

    test("⑤b弹窗压根没接上（confirmHandler 为 null）→ 同样一条都不执行（fail-closed）", () async {
      final fake = _FakePort();
      final executed = <String>[];
      final reported = <String>[];
      final scopes = <List<String>>[];
      expect(WorkflowActionBridge.confirmHandler, isNull, reason: "setUp 已清空：这就是 M4d-1 落码后的实际状态");
      final r = await runSteps(
          fake,
          scopedRunner(executed: executed, reported: reported, scopes: scopes),
          [_launch("com.demo.app"), _click("发送")]);
      expect(executed, isEmpty);
      expect(reported, isEmpty);
      expect(r.single["port_status"], DeviceActionExecutor.statusConfirmDenied);
      expect(r.single["message"], "confirm_denied");
      expect(fake.legacyCalls, 0);
    });

    test("⑥同意第一条、拒绝第二条 → 第一条执行并回报；第二条不执行不回报、第三条不提交、不回退", () async {
      final fake = _FakePort();
      final executed = <String>[];
      final reported = <String>[];
      final scopes = <List<String>>[];
      WorkflowActionBridge.confirmHandler =
          (capability) async => capability != DeviceActionExecutor.capTap;
      final r = await runSteps(
          fake,
          scopedRunner(executed: executed, reported: reported, scopes: scopes),
          [_launch("com.demo.app"), _click("发送"), _setText("你好")]);
      expect(executed, ["tk-1"], reason: "名单外的 tk-other 与后续条目都不该被执行");
      expect(reported, ["tk-1"]);
      expect(fake.submitted.length, 2, reason: "第二条没执行成功就不提交第三条");
      expect(r.length, 2);
      expect(r[0]["ok"], isTrue);
      expect(r[1]["port_status"], DeviceActionExecutor.statusConfirmDenied);
      expect(r[1]["message"], "confirm_denied");
      expect(fake.legacyCalls, 0);
    });

    test("⑦withConfirmHandler：运行前设、运行后复位（正常与异常路径都复位）", () async {
      final asked = <String>[];
      Future<bool> handler(String capability) async {
        asked.add(capability);
        return true;
      }

      expect(WorkflowActionBridge.confirmHandler, isNull);
      bool? effectiveDuring;
      final out = await WorkflowActionBridge.withConfirmHandler<String>(handler, () async {
        final h = WorkflowActionBridge.confirmHandler;
        effectiveDuring = h != null && await h.call("probe");
        return "ran";
      });
      expect(out, "ran");
      expect(effectiveDuring, isTrue, reason: "body 执行期间装的就是这个 handler（给得出同意）");
      expect(asked, ["probe"]);
      expect(WorkflowActionBridge.confirmHandler, isNull, reason: "跑完即摘：不许悬挂已卸载页面的 context");
      var threw = false;
      try {
        await WorkflowActionBridge.withConfirmHandler<String>(
            handler, () async => throw StateError("boom"));
      } catch (_) {
        threw = true;
      }
      expect(threw, isTrue, reason: "异常原样上抛，包装器不吞");
      expect(WorkflowActionBridge.confirmHandler, isNull, reason: "异常路径同样复位");
    });

    test("⑦b嵌套：内层跑完恢复外层 handler，而不是直接清空", () async {
      Future<bool> outer(String capability) async => true;
      Future<bool> inner(String capability) async => false;
      bool? effectiveAfterInner;
      await WorkflowActionBridge.withConfirmHandler<String>(outer, () async {
        await WorkflowActionBridge.withConfirmHandler<String>(inner, () async => "x");
        // 内层结束：拿得到同意＝恢复的是外层（内层永远给 false）；直接清空则拿到 false
        final h = WorkflowActionBridge.confirmHandler;
        effectiveAfterInner = h != null && await h.call("cap");
        return "y";
      });
      expect(effectiveAfterInner, isTrue);
      expect(WorkflowActionBridge.confirmHandler, isNull);
    });

    test("同时注入两个执行位时以带名单的为准（旧注入位保留给不区分条目的调用方）", () async {
      final fake = _FakePort();
      final executed = <String>[];
      final reported = <String>[];
      final scopes = <List<String>>[];
      var oldSlotCalls = 0;
      WorkflowActionBridge.confirmHandler = (capability) async => true;
      final r = await WorkflowActionBridge.runWorkflowSequence(
        steps: [_launch("com.demo.app"), _click("发送")],
        legacyStep: fake.legacyStep,
        bridgeEnabled: () async => true,
        submit: fake.submit,
        runner: (confirmed) async {
          oldSlotCalls++;
          return <Map<String, dynamic>>[];
        },
        scopedRunner: scopedRunner(executed: executed, reported: reported, scopes: scopes),
        log: fake.logs.add,
      );
      expect(oldSlotCalls, 0, reason: "带名单的注入位优先，否则『只执行本条』无从保证");
      expect(fake.runnerCalls, 0);
      expect(scopes, [["tk-1"], ["tk-2"]]);
      expect(executed, ["tk-1", "tk-2"]);
      expect(r.length, 2);
      expect(r.every((row) => row["ok"] == true), isTrue);
    });
  });

  // ── M4d-3 追加：感知页「工作流端口自检」用的最小可映射序列（既有 38 例一字未动）──
  group("M4d-3：端口自检序列（最小可映射工作流）", () {
    test("⑤自检序列满足可映射条件：首步 launch_app、两步都可映射、包名合法", () {
      final steps = workflowSelfCheckSteps("操作与记录");
      final inspected = inspectWorkflow(steps);
      expect(inspected.mappable, isTrue,
          reason: "自检序列自己都映射不了，这个入口就是摆设（reason=${inspected.reason}）");
      final plan = inspected.plan!;
      expect(plan.targetApp, kWorkflowSelfCheckTargetApp);
      expect(DeviceActionExecutor.isSafePackageName(plan.targetApp), isTrue);
      expect(plan.steps.map((s) => s.action).toList(), [wfActionLaunchApp, wfActionClick]);
      expect(plan.steps.map((s) => s.capability).toList(),
          [DeviceActionExecutor.capOpenApp, DeviceActionExecutor.capTap]);
      expect(planForWorkflow(steps), isNotNull);
    });

    test("⑤b点击目标原样当查询文本（不改写、不截断），且 target_app 取首步包名", () {
      final plan = planForWorkflow(workflowSelfCheckSteps("自 检 / 入口"))!;
      expect(plan.steps[1].payload["query"], "自 检 / 入口");
      expect(plan.steps[1].payload["by"], "text");
      expect(plan.steps[1].payload["target_app"], kWorkflowSelfCheckTargetApp);
      expect(plan.steps[1].target, "自 检 / 入口", reason: "回折结果里的 target 保留原始文本");
    });

    test("⑤c目标文本为空 → 不可映射并给出 missing_target（自检不猜要点什么）", () {
      expect(inspectWorkflow(workflowSelfCheckSteps("")).reason, WorkflowPlanReject.missingTarget);
    });

    test("⑥端口被拒 → 自检结论把 reason 原样带出，且一步都不回退本机路径", () async {
      final fake = _FakePort()
        ..verdict = (_, __) =>
            const ActionSubmitResult(allowed: false, reason: "target_not_allowed");
      final rows = await WorkflowActionBridge.runWorkflowSequence(
        steps: workflowSelfCheckSteps("操作与记录"),
        legacyStep: fake.legacyStep,
        bridgeEnabled: () async => true,
        submit: fake.submit,
        runner: fake.runner,
        log: fake.logs.add,
      );
      expect(fake.legacyCalls, 0, reason: "自检失败也不许偷偷回退旧执行器凑个能看的结论");
      expect(rows.single["via"], WorkflowActionBridge.viaPort);
      expect(rows.single["ok"], isFalse);
      expect(rows.single["message"], "target_not_allowed",
          reason: "UI 直接把这一字段展示给用户，必须原样");
      expect(rows.single["port_status"], "rejected");
      expect(
          fake.logs.any((l) => l.contains("abort step=1") && l.contains("target_not_allowed")),
          isTrue);
    });

    test("⑥b执行侧台账给出拒绝确认（confirm_denied）→ 同样原样带出、零本机执行", () async {
      // 假件返回的就是真机 `DeviceActionExecutor.runOnce` 在「拿不到同意」时那条台账行
      final fake = _FakePort()
        ..rowsFor = (_, token) => [
              {
                "action_token": token,
                "capability": DeviceActionExecutor.capOpenApp,
                "ok": false,
                "status": DeviceActionExecutor.statusConfirmDenied,
                "detail": "confirm_denied",
              }
            ];
      final rows = await WorkflowActionBridge.runWorkflowSequence(
        steps: workflowSelfCheckSteps("操作与记录"),
        legacyStep: fake.legacyStep,
        bridgeEnabled: () async => true,
        submit: fake.submit,
        runner: fake.runner,
        log: fake.logs.add,
      );
      expect(rows.single["message"], "confirm_denied");
      expect(rows.single["port_status"], DeviceActionExecutor.statusConfirmDenied);
      expect(fake.legacyCalls, 0);
      expect(fake.submitted.length, 1, reason: "第一条没过去就不提交第二条");
    });

    test("⑥c两步全成 → 两行 via=port、序号 1/2、能力与目标应用正确", () async {
      final fake = _FakePort();
      final rows = await WorkflowActionBridge.runWorkflowSequence(
        steps: workflowSelfCheckSteps("操作与记录"),
        legacyStep: fake.legacyStep,
        bridgeEnabled: () async => true,
        submit: fake.submit,
        runner: fake.runner,
        log: fake.logs.add,
      );
      expect(rows.length, 2);
      expect(rows.map((r) => r["via"]).toSet(), {WorkflowActionBridge.viaPort});
      expect(rows.map((r) => r["step"]).toList(), [1, 2]);
      expect(rows.map((r) => r["capability"]).toList(),
          [DeviceActionExecutor.capOpenApp, DeviceActionExecutor.capTap]);
      expect(rows.every((r) => r["ok"] == true), isTrue);
      expect(fake.legacyCalls, 0);
    });

    test("⑥d自检载荷同样不带 plugin（内置身份不接受自称）", () async {
      final fake = _FakePort();
      await WorkflowActionBridge.runWorkflowSequence(
        steps: workflowSelfCheckSteps("操作与记录"),
        legacyStep: fake.legacyStep,
        bridgeEnabled: () async => true,
        submit: fake.submit,
        runner: fake.runner,
        log: fake.logs.add,
      );
      expect(fake.submitted, isNotEmpty);
      for (final p in fake.submitted) {
        expect(p.containsKey("plugin"), isFalse);
      }
    });

    test("⑤d图节点带来的多余字段（id/x/y/ms）绝不进意图载荷——只有能力实际用到的字段才出门", () {
      // 无 edges 的图接上端口后，节点里除了 action/target 还带着画布字段（id、坐标、时长）
      final plan = planForWorkflow([
        {"action": wfActionLaunchApp, "target": "com.demo.app", "id": "n1"},
        {"action": wfActionClick, "target": "发送", "id": "n2", "x": 120, "y": 640, "ms": 300},
      ])!;
      expect(plan.steps[0].payload,
          {"capability": DeviceActionExecutor.capOpenApp, "target_app": "com.demo.app"});
      expect(plan.steps[1].payload, {
        "capability": DeviceActionExecutor.capTap,
        "target_app": "com.demo.app",
        "by": "text",
        "query": "发送",
      });
    });
  });
}
