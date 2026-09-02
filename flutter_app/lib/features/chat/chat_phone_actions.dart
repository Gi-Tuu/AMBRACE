// F7-b（2026-08-31）自 features/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/home_tab_controller.dart';
import '../../services/phone_perception_service.dart';

/// 手机感知动作流（AI 帮用户操作屏幕）：序列模板/节点选择/授权弹窗。
/// mixin on State：直接使用 mounted/context；不触碰屏幕私有字段。
mixin ChatPhoneActions<T extends StatefulWidget> on State<T> {  /// 3.4a：先尝试序列模板（帮我回/发/发布/点赞/播放），失败或不匹配再走单步节点选择。
  Future<String?> runPhoneAction(String userText) async {
    final l10n = AppLocalizations.of(context)!;
    if (!mounted) return null;
    final messenger = ScaffoldMessenger.of(context);
    // P1 工作流（2026-08-14）：用户自建序列（可含 Shizuku 系统级步骤）优先匹配
    final wf = await PhonePerceptionService.matchWorkflow(userText);
    if (wf != null && mounted) {
      final wfSteps = (wf['steps'] as List? ?? []).cast<Map>();
      if (wfSteps.isNotEmpty) {
        final wfName = wf['name'] as String? ?? l10n.wfDefaultName;
        final okRun = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.chatRunWf(wfName)),
            content: Text(l10n.chatWfSteps(wfSteps.length)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
              FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.input)),
            ],
          ),
        );
        if (okRun == true && mounted) {
          final wfResults = await PhonePerceptionService.executeActionSequence(wfSteps);
          final wfOk = wfResults.every((r) => r['ok'] == true);
          final wfSummary = wfResults
              .map((r) => l10n.chatWfStep(r['step'], r['ok'] == true ? '✓' : '✗', r['message'] ?? ''))
              .join('；');
          await PhonePerceptionService.uploadActionResult('workflow', wfName, wfOk, wfSummary);
          if (mounted) {
            messenger.showSnackBar(SnackBar(
              content: Text(wfOk ? l10n.chatWfDone(wfSummary) : l10n.chatWfInterrupted(wfSummary), maxLines: 3),
            ));
          }
        }
        return null;
      }
    }
    final tree = await PhonePerceptionService.getNodeTree();
    if (!mounted) return null;
    final serviceEnabled = tree["serviceEnabled"] as bool? ?? false;
    if (!serviceEnabled) {
      messenger.showSnackBar(SnackBar(content: Text(l10n.chatNoAccessibility)));
      return null;
    }

    // === 序列模板（3.4a） ===
    final template = PhonePerceptionService.parseActionTemplate(userText);
    if (template != null) {
      final steps = (template["steps"] as List? ?? []).cast<Map>();
      if (steps.isNotEmpty) {
        if (!PhonePerceptionService.autoAllowed) {
          final mode = await _showSequenceAuthDialog(template);
          if (!mounted) return null;
          if (mode == "reject" || mode == null) return null;
          if (mode == "minute") PhonePerceptionService.allowForMinute();
        }
        // 朋友圈相关模板：先切回朋友圈 tab 再执行（自家 app 内导航，轻度干涉不跨 app）
        final seqType = template["type"] as String? ?? "";
        if (seqType == "publish" || seqType == "like") {
          HomeTabController.switchTo(1);
          final nav = Navigator.of(context);
          if (nav.canPop()) nav.pop();
          await Future.delayed(const Duration(milliseconds: 1000));
        }
        final results = await PhonePerceptionService.executeActionSequence(steps);
        final allOk = results.every((r) => r["ok"] == true);
        final summary = results
            .map((r) => l10n.chatWfStep(r["step"], r["ok"] ? "✓" : "✗", r["message"]))
            .join("；");
        await PhonePerceptionService.uploadActionResult(
          "sequence",
          template["type"] as String? ?? "",
          allOk,
          summary,
        );
        messenger.showSnackBar(SnackBar(
          content: Text(allOk ? l10n.chatSeqDone(summary) : l10n.chatSeqInterrupted(summary), maxLines: 3),
        ));
        // 回复模板：文本已写入本 app 输入框，返回内容由发送流程代为发送（避免再点发送导致重复）
        return template["sendText"] as String?;
      }
    }

    final nodes = (tree["nodes"] as List? ?? []).cast<Map<dynamic, dynamic>>();
    final wantsLong = userText.contains("长按");
    final wantsInput = userText.contains("输入") ||
        userText.contains("回复") ||
        (userText.contains("发送") && userText.contains("文字"));

    // 目标节点：优先匹配用户消息里出现的节点文本（如“帮我点发送”→节点“发送”）
    Map<dynamic, dynamic>? target;
    if (!wantsInput) {
      for (final n in nodes) {
        final t = (n["text"] as String? ?? "").trim();
        if (t.isNotEmpty && userText.contains(t)) {
          target = n;
          break;
        }
      }
    }
    if (target == null && nodes.isNotEmpty) {
      final picked = await showModalBottomSheet<Map<dynamic, dynamic>>(
        context: context,
        builder: (ctx) => SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text(l10n.chatPickTarget,
                    style: const TextStyle(fontWeight: FontWeight.bold)),
              ),
              for (final n in nodes.take(30))
                ListTile(
                  dense: true,
                  title: Text(n["text"]?.toString() ?? ""),
                  subtitle: Text(n["editable"] == true ? l10n.nodeInput : l10n.nodeClickable),
                  onTap: () => Navigator.pop(ctx, n),
                ),
            ],
          ),
        ),
      );
      if (!mounted) return null;
      if (picked == null) return null;
      target = picked;
    }
    if (!mounted) return null;
    if (target == null) {
      messenger.showSnackBar(SnackBar(content: Text(l10n.chatNoNodes)));
      return null;
    }

    final targetText = (target["text"] as String? ?? "").trim();
    final String action;
    final String actionLabel;
    if (wantsInput) {
      action = "set_text";
      actionLabel = targetText.isEmpty ? l10n.nodeInput : targetText;
    } else if (wantsLong) {
      action = "long_click";
      actionLabel = targetText;
    } else {
      action = "click";
      actionLabel = targetText;
    }

    // 授权：允许本次 / 允许1分钟 / 拒绝（1分钟内已允许则跳过弹窗）
    if (!PhonePerceptionService.autoAllowed) {
      final mode = await _showActionAuthDialog(actionLabel, action == "set_text");
      if (!mounted) return null;
      if (mode == "reject" || mode == null) return null;
      if (mode == "minute") PhonePerceptionService.allowForMinute();
    }

    // 输入文本时额外询问内容（≤50 字）
    String? inputText;
    if (action == "set_text") {
      inputText = await _askInputText(actionLabel);
      if (!mounted) return null;
      if (inputText == null) return null;
    }

    final Map<dynamic, dynamic> res;
    if (action == "set_text") {
      res = await PhonePerceptionService.setTextOnFocus(inputText!);
    } else {
      res = await PhonePerceptionService.performAction(action, actionLabel);
    }
    final ok = res["ok"] as bool? ?? false;
    final msg = res["message"] as String? ?? l10n.chatOpDone;
    // 结果回传服务器（source=action_result），供聊天上下文引用与动作日志落库
    await PhonePerceptionService.uploadActionResult(
      action,
      actionLabel.isEmpty ? l10n.actionTarget : actionLabel,
      ok,
      msg,
    );
    if (mounted) {
      messenger.showSnackBar(SnackBar(content: Text(msg)));
    }
    return null;
  }

  /// 序列确认弹窗：展示每一步 + 干涉档位说明（轻度干涉默认）
  Future<String?> _showSequenceAuthDialog(Map<String, dynamic> template) async {
    final l10n = AppLocalizations.of(context)!;
    final steps = (template["steps"] as List? ?? []).cast<Map>();
    final typeLabel = switch (template["type"]) {
      "reply" => l10n.seqReply,
      "publish" => l10n.seqPublish,
      "like" => l10n.seqLike,
      "play" => l10n.seqPlay,
      _ => l10n.seqCombo,
    };
    final stepLines = steps.map((s) {
      if (s["action"] == "set_text") {
        return l10n.seqInputLine(s["text"]);
      }
      final verb = s["action"] == "long_click" ? l10n.seqLongClick : l10n.seqClick;
      return l10n.seqClickLine(verb, s["target"]);
    }).join("\n");
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatSeqTitle),
        content: Text(
          l10n.chatSeqDesc(typeLabel, stepLines, (template["type"] == "publish" || template["type"] == "like") ? l10n.chatSeqAutoNote : ""),
          style: const TextStyle(fontSize: 13, height: 1.5),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, "reject"), child: Text(l10n.reject)),
          TextButton(onPressed: () => Navigator.pop(ctx, "once"), child: Text(l10n.allowOnce)),
          FilledButton(onPressed: () => Navigator.pop(ctx, "minute"), child: Text(l10n.allowMinute)),
        ],
      ),
    );
  }

  Future<String?> _showActionAuthDialog(String targetLabel, bool isInput) async {
    final l10n = AppLocalizations.of(context)!;
    final op = isInput ? l10n.chatOpInput : (targetLabel.isEmpty ? l10n.chatOpDefault : l10n.chatOpTarget(targetLabel));
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatOpTitle),
        content: Text(l10n.chatOpDesc(op)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, "reject"), child: Text(l10n.reject)),
          TextButton(onPressed: () => Navigator.pop(ctx, "once"), child: Text(l10n.allowOnce)),
          FilledButton(onPressed: () => Navigator.pop(ctx, "minute"), child: Text(l10n.allowMinute)),
        ],
      ),
    );
  }

  Future<String?> _askInputText(String targetLabel) async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatInputTitle),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 50,
          decoration: InputDecoration(
            hintText: targetLabel.isEmpty ? l10n.chatInputHint : l10n.chatInputHintTarget(targetLabel),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.input)),
        ],
      ),
    );
    if (ok != true) {
      ctrl.dispose();
      return null;
    }
    final t = ctrl.text.trim();
    ctrl.dispose();
    return t.isEmpty ? null : t;
  }
}
