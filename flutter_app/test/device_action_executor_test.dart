import "package:flutter_test/flutter_test.dart";
import "package:shared_preferences/shared_preferences.dart";
import "package:ai_companion/services/device_action_executor.dart";
import "package:ai_companion/services/device_action_prefs.dart";
import "package:ai_companion/services/device_action_service.dart";
// M4d-3 追加（图工作流接端口 / 模板回落降噪需要这两个文件）
import "package:ai_companion/services/phone_perception_service.dart";
import "package:ai_companion/services/workflow_action_bridge.dart";

/// X7-M4b-2 内置行动执行器单测：只测纯逻辑 + runOnce 编排，
/// 真机路径靠注入假 `execute` 完全绕开（不碰 MethodChannel、不碰网络）。
Map<String, dynamic> _tap({
  String token = "t-tap",
  String target = "com.example.app",
  String by = "text",
  String query = "发送",
  bool dryRun = false,
}) =>
    {
      "action_token": token,
      "capability": DeviceActionExecutor.capTap,
      "action": "tap",
      "plugin": "builtin",
      "target_app": target,
      "by": by,
      "query": query,
      "text": null,
      "dry_run": dryRun,
      "status": "approved",
    };

Map<String, dynamic> _open({
  String token = "t-open",
  String target = "com.example.app",
  bool dryRun = false,
}) =>
    {
      "action_token": token,
      "capability": DeviceActionExecutor.capOpenApp,
      "action": "open_app",
      "plugin": "builtin",
      "target_app": target,
      "by": null,
      "query": null,
      "text": null,
      "dry_run": dryRun,
      "status": "approved",
    };

Map<String, dynamic> _setText({
  String token = "t-text",
  String text = "你好",
  bool dryRun = false,
}) =>
    {
      "action_token": token,
      "capability": DeviceActionExecutor.capSetText,
      "action": "set_text",
      "plugin": "builtin",
      "target_app": "com.example.app",
      "by": null,
      "query": null,
      "text": text,
      "dry_run": dryRun,
      "status": "approved",
    };

class _Spy {
  final List<Map<String, dynamic>> reports = [];
  final List<String> confirmed = [];
  final List<Map<String, dynamic>> executed = [];

  ActionConfirmFn confirm(bool grant) => (capability) async {
        confirmed.add(capability);
        return grant;
      };

  ActionReportFn report([bool grant = true]) => (token, ok, detail) async {
        reports.add({"token": token, "ok": ok, "detail": detail});
        return grant;
      };

  ActionExecuteFn execute(Map<String, dynamic> Function(Map<String, dynamic>) plan) => (intent) async {
        executed.add(intent);
        return plan(intent);
      };
}

/// X7-M4c-5 假策略源：三档 + 永久放行标记全部内存记账，测试不碰真 prefs。
class _Policy {
  final Map<String, ActionConfirmPolicy> policies = {};
  final Set<String> onceEver = {};
  final List<String> marked = [];
  int policyReads = 0;
  bool throwOnPolicyRead = false;

  ActionPolicyFn get policyOf => (capability) async {
        policyReads++;
        if (throwOnPolicyRead) throw StateError("prefs unavailable");
        return policies[capability] ?? ActionConfirmPolicy.firstPerType;
      };

  ActionOnceEverCheckFn get onceEverConfirmedOf =>
      (capability) async => onceEver.contains(capability);

  ActionMarkOnceEverFn get mark => (capability) async {
        marked.add(capability);
        return true;
      };
}

void main() {
  group("isSafePackageName", () {
    test("合法包名通过", () {
      expect(DeviceActionExecutor.isSafePackageName("com.example.app"), isTrue);
      expect(DeviceActionExecutor.isSafePackageName("com.gituu.ambrace.ai_companion"), isTrue);
      expect(DeviceActionExecutor.isSafePackageName("io.Flutter2.x_9"), isTrue);
      expect(DeviceActionExecutor.isSafePackageName("ab.cd"), isTrue);
    });

    test("空串 / 缺省返回 false", () {
      expect(DeviceActionExecutor.isSafePackageName(""), isFalse);
      expect(DeviceActionExecutor.isSafePackageName("   "), isFalse);
    });

    test("含空格 / 制表符不通过", () {
      expect(DeviceActionExecutor.isSafePackageName("com.a b"), isFalse);
      expect(DeviceActionExecutor.isSafePackageName(" com.a.b "), isFalse);
      expect(DeviceActionExecutor.isSafePackageName("com.a\tb"), isFalse);
    });

    test("shell 元字符与路径注入不通过", () {
      for (final bad in [
        "a;rm -rf",
        "com.a;reboot",
        "com.a|sh",
        "com.a&&curl",
        "com.a\$(id)",
        "com.a`id`",
        "com.a\nb",
        "/system/bin/sh",
        "com.a'b",
        'com.a"b',
      ]) {
        expect(DeviceActionExecutor.isSafePackageName(bad), isFalse, reason: bad);
      }
    });

    test("形态不合法（无点号 / 首字符数字 / 空段 / 非法段内字符）", () {
      for (final bad in ["com", "com.", ".com.a", "com..a", "1com.a", "com.a-b", "com.a_b-"]) {
        expect(DeviceActionExecutor.isSafePackageName(bad), isFalse, reason: bad);
      }
    });

    test("超长（>128 字符）不通过，恰好 128 通过", () {
      final ok128 = "c.${'a' * 126}";
      expect(ok128.length, DeviceActionExecutor.maxPackageNameLength);
      expect(DeviceActionExecutor.isSafePackageName(ok128), isTrue);
      expect(DeviceActionExecutor.isSafePackageName("$ok128.c"), isFalse);
    });
  });

  group("blockedReason", () {
    test("未登记能力一律拒绝", () {
      expect(
        DeviceActionExecutor.blockedReason({"capability": "action_screenshot", "target_app": "com.a.b"}),
        "unsupported_capability:action_screenshot",
      );
      expect(DeviceActionExecutor.blockedReason({"target_app": "com.a.b"}),
          "unsupported_capability:");
    });

    test("open_app：target_app 必须是合法包名", () {
      expect(DeviceActionExecutor.blockedReason(_open()), isNull);
      expect(DeviceActionExecutor.blockedReason(_open(target: "com.a b")),
          DeviceActionExecutor.reasonUnsafeTarget);
      expect(DeviceActionExecutor.blockedReason(_open(target: "抖音")),
          DeviceActionExecutor.reasonUnsafeTarget);
      expect(DeviceActionExecutor.blockedReason(_open(target: "")), DeviceActionExecutor.reasonNoTarget);
      expect(
          DeviceActionExecutor.blockedReason({"capability": DeviceActionExecutor.capOpenApp}),
          DeviceActionExecutor.reasonNoTarget);
    });

    test("tap：by 只认 text/id，query 必须非空", () {
      expect(DeviceActionExecutor.blockedReason(_tap()), isNull);
      expect(DeviceActionExecutor.blockedReason(_tap(by: "id")), isNull);
      expect(DeviceActionExecutor.blockedReason(_tap(by: "xy")), DeviceActionExecutor.reasonBadBy);
      expect(DeviceActionExecutor.blockedReason(_tap(by: "")), DeviceActionExecutor.reasonBadBy);
      expect(DeviceActionExecutor.blockedReason(_tap(query: "  ")), DeviceActionExecutor.reasonNoQuery);
      expect(DeviceActionExecutor.blockedReason(_tap(by: "coord", query: "")),
          DeviceActionExecutor.reasonBadBy);
    });

    test("set_text：text 必须非空", () {
      expect(DeviceActionExecutor.blockedReason(_setText()), isNull);
      expect(DeviceActionExecutor.blockedReason(_setText(text: "")), DeviceActionExecutor.reasonNoText);
      expect(DeviceActionExecutor.blockedReason(_setText(text: "  ")), DeviceActionExecutor.reasonNoText);
    });

    test("dry_run 一律返回 dry_run_skipped（优先于其它判定）", () {
      expect(DeviceActionExecutor.blockedReason(_open(dryRun: true)), DeviceActionExecutor.reasonDryRun);
      expect(DeviceActionExecutor.blockedReason(_open(target: "bad pkg", dryRun: true)),
          DeviceActionExecutor.reasonDryRun);
      expect(
          DeviceActionExecutor.blockedReason({"capability": "nope", "dry_run": true}),
          DeviceActionExecutor.reasonDryRun);
    });
  });

  group("needsConfirm", () {
    test("未确认过的类型要确认，确认过的放行", () {
      final confirmed = <String>{};
      expect(DeviceActionExecutor.needsConfirm(DeviceActionExecutor.capTap, confirmed), isTrue);
      confirmed.add(DeviceActionExecutor.capTap);
      expect(DeviceActionExecutor.needsConfirm(DeviceActionExecutor.capTap, confirmed), isFalse);
      expect(DeviceActionExecutor.needsConfirm(DeviceActionExecutor.capOpenApp, confirmed), isTrue);
    });
  });

  group("runOnce", () {
    test("pending 为空 → 什么都不做", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions(const []),
        report: spy.report(),
      );
      expect(rows, isEmpty);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(spy.confirmed, isEmpty);
    });

    test("拉取失败 → 记一条 fetch_failed，不执行不回报", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions(const [], error: "request_failed:connectionTimeout"),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusFetchFailed);
      expect(rows.single["detail"], contains("connectionTimeout"));
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
    });

    test("dry_run 条目既不执行也不回报", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open(dryRun: true)]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusDryRun);
      expect(rows.single["reported"], isFalse);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(spy.confirmed, isEmpty, reason: "干跑不该消耗首次确认");
    });

    test("执行成功 → 回报 ok:true", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true, "detail": "launched:com.example.app"}),
        fetchPending: () async => PendingActions([_open()]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusExecuted);
      expect(rows.single["ok"], isTrue);
      expect(rows.single["reported"], isTrue);
      expect(spy.reports, [
        {"token": "t-open", "ok": true, "detail": "launched:com.example.app"}
      ]);
      expect(spy.executed.single["target_app"], "com.example.app");
    });

    test("执行失败 → 回报 ok:false + detail（不得假装成功）", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": false, "detail": "accessibility_off"}),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusFailed);
      expect(rows.single["ok"], isFalse);
      expect(spy.reports.single["ok"], isFalse);
      expect(spy.reports.single["detail"], "accessibility_off");
    });

    test("执行器抛异常 → 不崩且回报 failed", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: (_) => throw StateError("channel blew up"),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusFailed);
      expect(rows.single["ok"], isFalse);
      expect(spy.reports.single["ok"], isFalse);
      expect(spy.reports.single["detail"], contains("executor_error"));
      expect(spy.reports.single["detail"], contains("channel blew up"));
    });

    test("首次确认后才执行；同类型第二次不再要确认", () async {
      final spy = _Spy();
      final confirmed = <String>{};
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        confirmedTypes: confirmed,
        fetchPending: () async => PendingActions([_tap(token: "a"), _tap(token: "b", query: "取消")]),
        report: spy.report(),
      );
      expect(spy.confirmed, [DeviceActionExecutor.capTap], reason: "两条同类只确认一次");
      expect(rows.map((r) => r["status"]),
          everyElement(DeviceActionExecutor.statusExecuted));
      expect(spy.reports.map((r) => r["token"]), ["a", "b"]);
      expect(confirmed, contains(DeviceActionExecutor.capTap));
    });

    test("用户拒绝确认 → 不执行、不回报", () async {
      final spy = _Spy();
      final confirmed = <String>{};
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(false),
        execute: spy.execute((_) => {"ok": true}),
        confirmedTypes: confirmed,
        fetchPending: () async => PendingActions([_open()]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusConfirmDenied);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(confirmed, isEmpty, reason: "没同意就不该写进已确认集合");
    });

    test("静态校验不过 → 不执行但如实回报 ok:false", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open(target: "com.a;rm -rf")]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusBlocked);
      expect(spy.executed, isEmpty);
      expect(spy.reports.single["ok"], isFalse);
      expect(spy.reports.single["detail"], DeviceActionExecutor.reasonUnsafeTarget);
    });

    test("缺 action_token → 拦下且无处回报", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open(token: "")]),
        report: spy.report(),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusBlocked);
      expect(rows.single["detail"], "missing_action_token");
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
    });

    test("回报本身失败 → 不抛异常，台账如实记 reported:false", () async {
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap()]),
        report: (token, ok, detail) => throw Exception("offline"),
      );
      expect(rows.single["status"], DeviceActionExecutor.statusExecuted);
      expect(rows.single["reported"], isFalse);
    });

    test("多条混合列表按序处理且互不影响", () async {
      final spy = _Spy();
      final items = [
        _open(token: "t1", dryRun: true),
        _open(token: "t2", target: "bad pkg"),
        _tap(token: "t3"),
        _setText(token: "t4", text: ""),
        _open(token: "t5"),
      ];
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((intent) => intent["capability"] == DeviceActionExecutor.capTap
            ? throw StateError("boom")
            : {"ok": true, "detail": "done"}),
        fetchPending: () async => PendingActions(items),
        report: spy.report(),
      );
      expect(rows.map((r) => r["action_token"]), ["t1", "t2", "t3", "t4", "t5"]);
      expect(rows.map((r) => r["status"]), [
        DeviceActionExecutor.statusDryRun,
        DeviceActionExecutor.statusBlocked,
        DeviceActionExecutor.statusFailed,
        DeviceActionExecutor.statusBlocked,
        DeviceActionExecutor.statusExecuted,
      ]);
      // 只有该回报的四条会打到服务端（干跑那条除外），且顺序一致
      expect(spy.reports.map((r) => r["token"]), ["t2", "t3", "t4", "t5"]);
      expect(spy.reports.map((r) => r["ok"]), [false, false, false, true]);
      // 每种动作各确认一次
      expect(spy.confirmed, [DeviceActionExecutor.capTap, DeviceActionExecutor.capOpenApp]);
      // 实际执行到的只有 t3 / t5（t1 干跑、t2 t4 静态校验就拦下）
      expect(spy.executed.map((i) => i["action_token"]), ["t3", "t5"]);
    });

    test("已确认集合跨轮次复用：第二轮同类不再问", () async {
      final spy = _Spy();
      final confirmed = <String>{};
      Future<List<Map<String, dynamic>>> once(List<Map<String, dynamic>> items) =>
          DeviceActionExecutor.runOnce(
            confirm: spy.confirm(true),
            execute: spy.execute((_) => {"ok": true}),
            confirmedTypes: confirmed,
            fetchPending: () async => PendingActions(items),
            report: spy.report(),
          );

      await once([_tap()]);
      await once([_tap(token: "b"), _open(token: "c")]);
      expect(spy.confirmed, [DeviceActionExecutor.capTap, DeviceActionExecutor.capOpenApp]);
    });

    test("detail 过长截断（审计单行不爆）", () async {
      final spy = _Spy();
      await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": false, "detail": "x" * 500}),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
      );
      expect(
        (spy.reports.single["detail"] as String).length,
        DeviceActionExecutor.maxDetailLength,
      );
    });
  });

  group("policyName / policyFromName（三档键值）", () {
    test("三档名称往返一致", () {
      for (final p in ActionConfirmPolicy.values) {
        expect(policyFromName(policyName(p)), p, reason: policyName(p));
      }
      expect(policyName(ActionConfirmPolicy.onceEver), "once_ever");
      expect(policyName(ActionConfirmPolicy.firstPerType), "first_per_type");
      expect(policyName(ActionConfirmPolicy.everyTime), "every_time");
    });

    test("未知/空/null/脏名一律回落中档，且默认档就是中档", () {
      for (final bad in ["", "   ", "nope", "ONCE_EVER", "first_per_session", "once", "every"]) {
        expect(policyFromName(bad), ActionConfirmPolicy.firstPerType, reason: bad);
      }
      expect(policyFromName(null), ActionConfirmPolicy.firstPerType);
      expect(DeviceActionPrefs.defaultPolicy, ActionConfirmPolicy.firstPerType);
    });
  });

  group("shouldConfirm（三档纯判定）", () {
    test("轻：未标永久放行仍要问；标过就不再问（与会话集合无关）", () {
      final session = <String>{};
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capTap,
          ActionConfirmPolicy.onceEver,
          onceEverConfirmed: false,
          sessionConfirmed: session,
        ),
        isTrue,
      );
      session.add(DeviceActionExecutor.capTap);
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capTap,
          ActionConfirmPolicy.onceEver,
          onceEverConfirmed: true,
          sessionConfirmed: session,
        ),
        isFalse,
      );
    });

    test("中：沿用会话集合语义（第一次问、第二次不问），且不看永久放行标记", () {
      final session = <String>{};
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capOpenApp,
          ActionConfirmPolicy.firstPerType,
          onceEverConfirmed: true,
          sessionConfirmed: session,
        ),
        isTrue,
      );
      session.add(DeviceActionExecutor.capOpenApp);
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capOpenApp,
          ActionConfirmPolicy.firstPerType,
          onceEverConfirmed: false,
          sessionConfirmed: session,
        ),
        isFalse,
      );
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capTap,
          ActionConfirmPolicy.firstPerType,
          onceEverConfirmed: true,
          sessionConfirmed: session,
        ),
        isTrue,
        reason: "中档只认会话集合：没确认过的另一类仍要问",
      );
    });

    test("重：恒确认（永久放行标过、会话确认过也照问）", () {
      expect(
        DeviceActionExecutor.shouldConfirm(
          DeviceActionExecutor.capTap,
          ActionConfirmPolicy.everyTime,
          onceEverConfirmed: true,
          sessionConfirmed: {DeviceActionExecutor.capTap},
        ),
        isTrue,
      );
    });
  });

  group("runOnce × 三档策略", () {
    test("轻：未标过 → 确认后落永久放行标记并执行回报", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capTap] = ActionConfirmPolicy.onceEver;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(spy.confirmed, [DeviceActionExecutor.capTap]);
      expect(p.marked, [DeviceActionExecutor.capTap]);
      expect(rows.single["status"], DeviceActionExecutor.statusExecuted);
      expect(spy.reports.single["ok"], isTrue);
    });

    test("轻：已标过 → 不再问、不重复标记，直接执行", () async {
      final spy = _Spy();
      final p = _Policy()
        ..policies[DeviceActionExecutor.capTap] = ActionConfirmPolicy.onceEver
        ..onceEver.add(DeviceActionExecutor.capTap);
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(spy.confirmed, isEmpty);
      expect(p.marked, isEmpty);
      expect(rows.single["status"], DeviceActionExecutor.statusExecuted);
      expect(spy.executed, hasLength(1));
    });

    test("重：同一会话两条同类各确认一次；中档才不落永久标记", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capTap] = ActionConfirmPolicy.everyTime;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap(token: "a"), _tap(token: "b", query: "取消")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(spy.confirmed, [DeviceActionExecutor.capTap, DeviceActionExecutor.capTap],
          reason: "重档每次都问");
      expect(p.marked, isEmpty, reason: "只有轻档才落永久放行标记");
      expect(rows.map((r) => r["status"]),
          [DeviceActionExecutor.statusExecuted, DeviceActionExecutor.statusExecuted]);

      final spy2 = _Spy();
      final p2 = _Policy(); // 缺省即中档
      await DeviceActionExecutor.runOnce(
        confirm: spy2.confirm(true),
        execute: spy2.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap(token: "a"), _tap(token: "b", query: "取消")]),
        report: spy2.report(),
        policyOf: p2.policyOf,
        onceEverConfirmedOf: p2.onceEverConfirmedOf,
        markOnceEverConfirmed: p2.mark,
      );
      expect(spy2.confirmed, [DeviceActionExecutor.capTap], reason: "中档同会话第二次不问");
      expect(p2.marked, isEmpty);
    });

    test("重：拒绝确认 → 不执行、不回报、不落标记（回归）", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capOpenApp] = ActionConfirmPolicy.everyTime;
      final confirmed = <String>{};
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(false),
        execute: spy.execute((_) => {"ok": true}),
        confirmedTypes: confirmed,
        fetchPending: () async => PendingActions([_open()]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.single["status"], DeviceActionExecutor.statusConfirmDenied);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(p.marked, isEmpty);
      expect(confirmed, isEmpty);
    });

    test("档位读不到（policyOf 抛异常）→ fail-closed：照样要确认", () async {
      final spy = _Spy();
      final p = _Policy()..throwOnPolicyRead = true;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(false),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open()]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(p.policyReads, 1);
      expect(spy.confirmed, [DeviceActionExecutor.capOpenApp], reason: "读不到档位也不能静默放行");
      expect(rows.single["status"], DeviceActionExecutor.statusConfirmDenied);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
    });

    test("重档下 dry_run 仍不执行、不回报，也不消耗确认/不读档位", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capOpenApp] = ActionConfirmPolicy.everyTime;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open(dryRun: true)]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.single["status"], DeviceActionExecutor.statusDryRun);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(spy.confirmed, isEmpty);
      expect(p.policyReads, 0);
      expect(p.marked, isEmpty);
    });

    test("策略不影响静态校验：重档下非法包名依旧先拦下并如实回报", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capOpenApp] = ActionConfirmPolicy.everyTime;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_open(target: "com.a;rm -rf")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.single["status"], DeviceActionExecutor.statusBlocked);
      expect(spy.confirmed, isEmpty, reason: "静态校验在确认之前");
      expect(spy.executed, isEmpty);
      expect(spy.reports.single["ok"], isFalse);
      expect(spy.reports.single["detail"], DeviceActionExecutor.reasonUnsafeTarget);
    });

    test("策略不影响如实回报：轻档下执行失败照样回报 ok:false", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capTap] = ActionConfirmPolicy.onceEver;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": false, "detail": "accessibility_off"}),
        fetchPending: () async => PendingActions([_tap()]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.single["status"], DeviceActionExecutor.statusFailed);
      expect(rows.single["ok"], isFalse);
      expect(spy.reports.single["ok"], isFalse);
      expect(spy.reports.single["detail"], "accessibility_off");
      expect(p.marked, [DeviceActionExecutor.capTap], reason: "用户确实同意过，标记与执行结果无关");
    });

    test("默认档＝中档：不注入策略时走 DeviceActionPrefs（本机没配过 → 中档）", () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
      final spy = _Spy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap(token: "a"), _tap(token: "b", query: "取消")]),
        report: spy.report(),
      );
      expect(spy.confirmed, [DeviceActionExecutor.capTap], reason: "缺省＝中档：同类两条只问一次");
      expect(rows.map((r) => r["status"]),
          [DeviceActionExecutor.statusExecuted, DeviceActionExecutor.statusExecuted]);
      expect(await DeviceActionPrefs.isOnceEverConfirmed(DeviceActionExecutor.capTap), isFalse,
          reason: "中档不落永久放行标记");
    });

    test("prefs 只存策略名/能力名：键位口径与脏值回落", () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({
        "device_action_policy_action_tap": "once_ever",
        "device_action_policy_action_open_app": "every_day",
        "device_action_once_ever_action_set_text": true,
      });
      expect(await DeviceActionPrefs.policyFor(DeviceActionExecutor.capTap),
          ActionConfirmPolicy.onceEver);
      expect(await DeviceActionPrefs.policyFor(DeviceActionExecutor.capOpenApp),
          ActionConfirmPolicy.firstPerType, reason: "脏值回落中档");
      expect(await DeviceActionPrefs.policyFor(DeviceActionExecutor.capSetText),
          ActionConfirmPolicy.firstPerType, reason: "没配过＝中档");
      expect(await DeviceActionPrefs.isOnceEverConfirmed(DeviceActionExecutor.capSetText), isTrue);
      expect(await DeviceActionPrefs.isOnceEverConfirmed(DeviceActionExecutor.capTap), isFalse);
      expect(
        await DeviceActionPrefs.setPolicy(DeviceActionExecutor.capSetText, ActionConfirmPolicy.everyTime),
        isTrue,
      );
      expect(await DeviceActionPrefs.policyFor(DeviceActionExecutor.capSetText),
          ActionConfirmPolicy.everyTime);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getKeys().every((k) => !k.contains("com.example")), isTrue,
          reason: "绝不落动作内容/包名，只落能力名与策略名");
    });
  });

  // ── M4d-2 追加：onlyTokens＝「只执行本条」（既有 41 例一字未动）──
  group("runOnce × onlyTokens（只执行本条）", () {
    test("①命中 → 只执行该条；名单外条目不执行、不回报、未被消费", () async {
      final spy = _Spy();
      final p = _Policy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        onlyTokens: {"t2"},
        fetchPending: () async =>
            PendingActions([_open(token: "t1"), _tap(token: "t2"), _setText(token: "t3")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.map((r) => r["action_token"]), ["t2"], reason: "台账只含命中的那一条");
      expect(spy.executed.map((i) => i["action_token"]), ["t2"]);
      expect(spy.reports.map((r) => r["token"]), ["t2"], reason: "名单外不回报＝原样留在服务端队列");
      expect(spy.confirmed, [DeviceActionExecutor.capTap], reason: "名单外的条目连首次确认都不该消耗");
      expect(rows.single["status"], DeviceActionExecutor.statusExecuted);
    });

    test("②不传 onlyTokens → 仍处理拉到的全部待办（回归：缺省语义零改动）", () async {
      final spy = _Spy();
      final p = _Policy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        fetchPending: () async => PendingActions([_tap(token: "a"), _open(token: "b"), _setText(token: "c")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.map((r) => r["action_token"]), ["a", "b", "c"]);
      expect(spy.executed.map((i) => i["action_token"]), ["a", "b", "c"]);
      expect(spy.reports.map((r) => r["token"]), ["a", "b", "c"]);
    });

    test("③传空集 → 一条都不执行（不执行、不回报、不要确认，也不报错）", () async {
      final spy = _Spy();
      final p = _Policy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        onlyTokens: <String>{},
        fetchPending: () async => PendingActions([_tap(token: "a"), _open(token: "b")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows, isEmpty);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(spy.confirmed, isEmpty);
    });

    test("⑧名单里的 token 不在待办 → 什么也不做、不崩（空台账）", () async {
      final spy = _Spy();
      final p = _Policy();
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(true),
        execute: spy.execute((_) => {"ok": true}),
        onlyTokens: {"not-in-queue"},
        fetchPending: () async => PendingActions([_tap(token: "a"), _open(token: "b")]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows, isEmpty);
      expect(spy.executed, isEmpty);
      expect(spy.reports, isEmpty);
      expect(spy.confirmed, isEmpty);
    });

    test("命中条目的既有语义一律不变：干跑不回报 / 静态校验如实回报 / 拒绝确认不回报", () async {
      final spy = _Spy();
      final p = _Policy()..policies[DeviceActionExecutor.capOpenApp] = ActionConfirmPolicy.everyTime;
      final rows = await DeviceActionExecutor.runOnce(
        confirm: spy.confirm(false),
        execute: spy.execute((_) => {"ok": true}),
        onlyTokens: {"t-dry", "t-bad", "t-open"},
        fetchPending: () async => PendingActions([
          _open(token: "t-dry", dryRun: true),
          _open(token: "t-bad", target: "com.a;rm -rf"),
          _open(token: "t-open"),
          _tap(token: "t-outside"), // 名单外：合法也不该被动一根手指
        ]),
        report: spy.report(),
        policyOf: p.policyOf,
        onceEverConfirmedOf: p.onceEverConfirmedOf,
        markOnceEverConfirmed: p.mark,
      );
      expect(rows.map((r) => r["action_token"]), ["t-dry", "t-bad", "t-open"],
          reason: "台账仍按 pending 原序，只少掉名单外的条目");
      expect(rows.map((r) => r["status"]), [
        DeviceActionExecutor.statusDryRun,
        DeviceActionExecutor.statusBlocked,
        DeviceActionExecutor.statusConfirmDenied,
      ]);
      expect(spy.executed, isEmpty);
      expect(spy.reports.map((r) => r["token"]), ["t-bad"],
          reason: "干跑与拒绝确认都不回报；名单外那条更不回报");
      expect(p.marked, isEmpty, reason: "没同意就不落永久放行标记");
    });
  });

  // ── M4d-3 追加：图工作流「无 edges」接端口 + 序列模板回落 INFO 降噪（既有 46 例一字未动）──
  group("M4d-3：图工作流无 edges 分支接行动端口", () {
    Map<String, dynamic> launch([String pkg = "com.demo.app"]) =>
        {"action": wfActionLaunchApp, "target": pkg};
    Map<String, dynamic> click(String t) => {"action": wfActionClick, "target": t};

    test("①无 edges（空列表）→ 整条走端口：两步各提交一次、结论 via=port、旧执行器零调用", () async {
      SharedPreferences.setMockInitialValues({DeviceActionPrefs.workflowBridgeKey: true});
      final p = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), click("发送")],
        edges: const [],
        legacyStep: p.legacyStep,
        bridgeEnabled: () async => true,
        submit: p.submit,
        runner: p.runner,
        log: p.logs.add,
      );
      expect(await DeviceActionPrefs.isWorkflowBridgeEnabled(), isTrue);
      expect(p.submitted.length, 2, reason: "与 executeActionSequence 同一条路：逐步提交");
      expect(p.submitted.map((s) => s["capability"]).toList(),
          [DeviceActionExecutor.capOpenApp, DeviceActionExecutor.capTap]);
      expect(p.legacyCalls, 0, reason: "走端口就不该再碰本机执行器");
      expect(rows.map((r) => r["via"]).toList(),
          [WorkflowActionBridge.viaPort, WorkflowActionBridge.viaPort]);
      expect(rows.map((r) => r["step"]).toList(), [1, 2]);
      expect(rows.map((r) => r["action"]).toList(), [wfActionLaunchApp, wfActionClick]);
      expect(rows.map((r) => r["target"]).toList(), ["com.demo.app", "发送"]);
      expect(p.logs.any((l) => l.startsWith("via_port")), isTrue);
    });

    test("①b edges 整个不传（null）＝同一条路；开关关时仍走旧路径", () async {
      SharedPreferences.setMockInitialValues({});
      final onPort = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), click("发送")],
        legacyStep: onPort.legacyStep,
        bridgeEnabled: () async => true,
        submit: onPort.submit,
        runner: onPort.runner,
        log: onPort.logs.add,
      );
      expect(rows.every((r) => r["via"] == WorkflowActionBridge.viaPort), isTrue);
      expect(onPort.submitted.length, 2);

      final offPort = _PerceptionPort();
      final legacyRows = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), click("发送")],
        legacyStep: offPort.legacyStep,
        bridgeEnabled: () async => false,
        submit: offPort.submit,
        runner: offPort.runner,
        log: offPort.logs.add,
      );
      expect(offPort.submitted, isEmpty, reason: "开关关＝旧路径逐字不变（缺省就是关）");
      expect(offPort.legacyCalls, 2);
      expect(legacyRows.every((r) => r["via"] == WorkflowActionBridge.viaLegacy), isTrue);
    });

    test("②无 edges 但含 tap_xy → 整条回落旧路径，旧执行器被逐步调用且一次端口都不提交", () async {
      SharedPreferences.setMockInitialValues({DeviceActionPrefs.workflowBridgeKey: true});
      final p = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), {"action": "tap_xy", "x": 10, "y": 20, "target": ""}],
        edges: const [],
        legacyStep: p.legacyStep,
        bridgeEnabled: () async => true,
        submit: p.submit,
        runner: p.runner,
        log: p.logs.add,
      );
      expect(p.submitted, isEmpty, reason: "任一步不可映射＝整条走旧路径，不做半新半旧");
      expect(p.legacyCalls, 2, reason: "旧执行器被真正逐步调用");
      expect(rows.map((r) => r["via"]).toList(),
          [WorkflowActionBridge.viaLegacy, WorkflowActionBridge.viaLegacy]);
      expect(rows.last["action"], "tap_xy");
      expect(
          p.logs
              .any((l) => l.contains("fallback_legacy") && l.contains("unsupported_step:tap_xy")),
          isTrue,
          reason: "回落必须给出机器可读原因");
    });

    test("②b不注入旧执行器时默认值就是真机 _executeSingleStep（非 Android 主机必然失败而非崩溃）",
        () async {
      SharedPreferences.setMockInitialValues({});
      final p = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [click("某个按钮")],
        edges: const [],
        bridgeEnabled: () async => true,
        submit: p.submit,
        runner: p.runner,
        log: p.logs.add,
      );
      expect(p.submitted, isEmpty);
      expect(rows.single["via"], WorkflowActionBridge.viaLegacy);
      expect(rows.single["action"], wfActionClick);
      expect(rows.single["ok"], isFalse, reason: "本机通道在测试主机上不可用，说明真跑了默认实现");
      expect((rows.single["message"] as String).isNotEmpty, isTrue);
    });

    test("③有 edges 的图 → 恒走旧路径、不提交端口，并记 reason=branching_graph（回归）", () async {
      SharedPreferences.setMockInitialValues({DeviceActionPrefs.workflowBridgeKey: true});
      final p = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [
          {"id": "n1", ...launch("com.demo.app")},
          {"id": "n2", ...click("发送")},
        ],
        edges: [
          {"from": "n1", "to": "n2", "type": "success"}
        ],
        legacyStep: p.legacyStep,
        bridgeEnabled: () async => true,
        submit: p.submit,
        runner: p.runner,
        log: p.logs.add,
      );
      expect(p.submitted, isEmpty, reason: "分支/条件/循环图映射不成线性意图列表");
      expect(p.legacyCalls, 0,
          reason: "有 edges 那条路逐字调 _executeSingleStep，注入位根本插不进去＝旧路径没被动过");
      expect(rows.length, 1, reason: "首步失败且不满足任何连线条件 → 不沿边前进");
      expect(rows.first["action"], wfActionLaunchApp);
      expect(rows.first["ok"], isFalse,
          reason: "非 Android 主机上真实单步必然失败，说明跑的是真机默认实现而非假件");
      expect((rows.first["message"] as String).isNotEmpty, isTrue);
      expect(rows.first.containsKey("via"), isFalse, reason: "有 edges 的旧路径返回结构逐字不变");
      expect(p.logs.any((l) => l.contains("fallback_legacy") && l.contains("branching_graph")),
          isTrue,
          reason: "走旧路径也要看得见原因");
    });

    test("④序列模板回落 INFO：同一模板一次进程内只记一条，非模板序列照记", () async {
      SharedPreferences.setMockInitialValues({});
      List<Map> stepsOf(String text) =>
          (PhonePerceptionService.parseActionTemplate(text)!["steps"] as List).cast<Map>();

      Future<int> fallbackLines(List<Map> steps, int times) async {
        final p = _PerceptionPort();
        for (var i = 0; i < times; i++) {
          await PhonePerceptionService.executeActionSequence(
            steps,
            legacyStep: p.legacyStep,
            bridgeEnabled: () async => true,
            submit: p.submit,
            runner: p.runner,
            log: p.logs.add,
          );
        }
        expect(p.submitted, isEmpty, reason: "模板恒走旧路径（by design 不开映射）");
        expect(p.legacyCalls, steps.length * times, reason: "降噪只影响日志，旧路径每步照跑");
        return p.logs.where((l) => l.contains("fallback_legacy")).length;
      }

      expect(await fallbackLines(stepsOf('帮我回"你好"'), 3), 1, reason: "同一模板连触三次只刷一条");
      expect(await fallbackLines(stepsOf("点赞"), 2), 1, reason: "另一个模板各自有一条");
      // 用户自己画的序列（不是内置模板）不受降噪影响：每次都要看得见原因
      expect(await fallbackLines([click("某个用户自定义按钮")], 2), 2);
    });

    test("⑦无 edges 的图与 executeActionSequence 返回结构一致（字段集合、顺序、取值同构）", () async {
      final steps = [launch("com.demo.app"), click("发送")];
      for (final onPort in [true, false]) {
        final g = _PerceptionPort();
        final s = _PerceptionPort();
        final graphRows = await PhonePerceptionService.executeWorkflowGraph(
          steps,
          edges: const [],
          legacyStep: g.legacyStep,
          bridgeEnabled: () async => onPort,
          submit: g.submit,
          runner: g.runner,
          log: g.logs.add,
        );
        final seqRows = await PhonePerceptionService.executeActionSequence(
          steps,
          legacyStep: s.legacyStep,
          bridgeEnabled: () async => onPort,
          submit: s.submit,
          runner: s.runner,
          log: s.logs.add,
        );
        expect(graphRows.length, seqRows.length);
        for (var i = 0; i < seqRows.length; i++) {
          expect(graphRows[i].keys.toSet(), seqRows[i].keys.toSet(),
              reason: "onPort=$onPort 第 $i 行字段集合必须一致");
          for (final k in ["step", "action", "target", "ok", "message"]) {
            expect(graphRows[i][k], seqRows[i][k], reason: "$k 不一致（onPort=$onPort）");
          }
        }
        expect(
            graphRows.first.keys.toSet().containsAll({"step", "action", "target", "ok", "message"}),
            isTrue,
            reason: "旧实现那五个字段一个都不能少");
      }
    });

    test("⑧桥不可用时 no-edges 分支不抛异常（开关读失败＝旧路径；提交通道炸＝结构化失败行）", () async {
      SharedPreferences.setMockInitialValues({});
      final p = _PerceptionPort();
      final rows = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), click("发送")],
        edges: const [],
        legacyStep: p.legacyStep,
        bridgeEnabled: () async => throw StateError("prefs 不可用"),
        submit: p.submit,
        runner: p.runner,
        log: p.logs.add,
      );
      expect(rows.length, 2);
      expect(rows.every((r) => r["via"] == WorkflowActionBridge.viaLegacy), isTrue,
          reason: "开关读失败＝关＝旧路径（与 prefs 缺省口径一致）");

      final p2 = _PerceptionPort();
      final boom = await PhonePerceptionService.executeWorkflowGraph(
        [launch("com.demo.app"), click("发送")],
        edges: const [],
        legacyStep: p2.legacyStep,
        bridgeEnabled: () async => true,
        submit: (payload) async => throw StateError("断网"),
        runner: p2.runner,
        log: p2.logs.add,
      );
      expect(boom.single["ok"], isFalse);
      expect(boom.single["message"], contains("port_submit_failed"));
      expect(boom.single["via"], WorkflowActionBridge.viaPort);
      expect(p2.legacyCalls, 0, reason: "拿不到裁决也不回退旧路径");
      expect(p2.logs.any((l) => l.contains("port_submit_failed")), isTrue);
    });
  });
}

/// M4d-3：`PhonePerceptionService` 注入位假件（记账用）。与桥单测里的 `_FakePort` 同口径但不共享代码：
/// 本文件验的是「图/序列入口怎么分发」，不是桥内部行为。
class _PerceptionPort {
  final List<Map<String, dynamic>> submitted = [];
  final List<String> logs = [];
  int legacyCalls = 0;

  LegacyStepExecutor get legacyStep => (step, stepNo) async {
        legacyCalls++;
        return {
          "step": stepNo,
          "action": step["action"] ?? "",
          "target": step["target"] ?? "",
          "ok": true,
          "message": "legacy:$stepNo",
        };
      };

  WorkflowIntentSubmitter get submit => (payload) async {
        submitted.add(Map<String, dynamic>.from(payload));
        return ActionSubmitResult(
            allowed: true, status: "approved", actionToken: "tk-${submitted.length}");
      };

  WorkflowPendingRunner get runner => (confirmed) async {
        final i = submitted.length;
        return [
          {
            "action_token": "tk-$i",
            "capability": submitted[i - 1]["capability"] ?? "",
            "ok": true,
            "status": DeviceActionExecutor.statusExecuted,
            "detail": "done:$i",
          }
        ];
      };
}
