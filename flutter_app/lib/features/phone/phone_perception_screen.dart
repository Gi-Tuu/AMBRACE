import "dart:async";
import "package:flutter/material.dart";
import "package:flutter/services.dart";
import "package:geolocator/geolocator.dart";
import "../../services/notification_service.dart";
import "../../utils/beijing_time.dart";
import "../../services/phone_perception_service.dart";
import "../../services/api_client.dart";
import "../../services/device_action_service.dart";
import "../../services/device_action_executor.dart";
import "../../services/device_action_prefs.dart";
import "../../services/workflow_action_bridge.dart";
// 注：本页是**用户自己的系统级感知**，不接角色隐私上锁（见下方字段处说明）
import "../../features/phone/perception_tiles.dart";
import "../../features/settings/notification_whitelist_screen.dart";
import "shizuku_screen.dart";
import "workflow_screen.dart";
import "../../services/shizuku_service.dart";
import "../../utils/app_lang.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "package:ai_companion/theme/tokens.dart";

/// 手机感知设置页（AI 走出沙箱 Phase 1）：总开关 + 逐项授权 + 权限引导 + 历史记录
class PhonePerceptionScreen extends StatefulWidget {
  const PhonePerceptionScreen({super.key});

  @override
  State<PhonePerceptionScreen> createState() => _PhonePerceptionScreenState();
}

class _PhonePerceptionScreenState extends State<PhonePerceptionScreen> with WidgetsBindingObserver {
  bool _enabled = false;
  bool _screenOn = false;
  bool _clipboardOn = false;
  bool _mediaOn = false;
  bool _mediaFilesOn = false;
  bool _notificationOn = false;
  bool _autoNotifyOn = false;
  bool _actionsOn = false;
  bool _serviceEnabled = false;
  bool _notifServiceEnabled = false;
  bool _historyLoading = false;
  List<Map<String, dynamic>> _history = [];
  bool _showHistory = false;
  // 本页**不做隐私上锁**（2026-09-22 用户拍板）：隐私上锁只锁角色侧内容（角色日记 / 内心 / 角色的小手机），
  // 感知快照是用户自己的东西、且是系统级数据，不该被角色拦住查看。原实现按 target="phone" + characterId=0
  // （服务端解析最近互动角色）在这里挂 PrivacyLockView，会出现「某角色的锁把用户自己的感知锁住」的错觉。
  bool _locationEnabled = false;
  bool _locationGpsEnabled = false;
  bool _locationFollow = false;
  String _userLocation = "";
  String _aiLocation = "";
  double? _locationLat;
  double? _locationLng;
  String _locationCity = "";
  bool _usageStatsOn = false;
  bool _usageStatsGranted = false;
  bool _pendingUsageGrant = false; // 跳转系统「使用情况访问」等待用户授权后自动重查
  Timer? _usageTimer;
  bool _shizukuServer = false;
  bool _shizukuGranted = false;
  bool _shizukuBusy = false;
  String _shizukuSnapshot = "";
  bool _expandScreen = false; // 读屏子项（剪贴板/相册/媒体文件）
  bool _expandActions = false; // 模拟操作子项（查看节点）
  bool _expandNotif = false; // 通知读取子项（主动提及/白名单）
  bool _expandLocation = false; // 位置信息子项
  Map<String, dynamic> _health = {}; // R5：统一健康检测
  bool _batteryOk = false; // R4：电池白名单
  // M4b-2：行动执行器「每类首次确认」的已确认集合（决策④，随本页生命周期，退出页面即重来）
  final Set<String> _actionConfirmed = <String>{};
  // M4d：工作流是否改走行动端口（缺省＝关；只影响整条可映射的工作流，见 workflow_action_bridge）
  bool _workflowBridgeOn = false;
  // M4b-2 收尾：本机自检目标（= build.gradle 的 applicationId；打开自己无副作用）
  static const String _selfActionTarget = "com.gituu.ambrace.ai_companion";

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    NotificationService().setActiveScreen(ActiveScreen.other);
    _load();
    _reportTimezone();
    _loadLocation();
    _loadUsageStatsState();
    _loadShizukuState();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _usageTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 从系统「使用情况访问」授权页返回时自动重查并开启，无需用户再点一次开关
    if (state == AppLifecycleState.resumed && _pendingUsageGrant) {
      _autoEnableUsageStatsAfterReturn();
    }
    // R4：从电池优化设置返回时刷新状态
    if (state == AppLifecycleState.resumed) {
      _loadHealth();
    }
  }

  Future<void> _load() async {
    final prefsEnabled = await PhonePerceptionService.isEnabled();
    final screen = await PhonePerceptionService.subEnabled(PhonePerceptionService.screenKey);
    final clip = await PhonePerceptionService.subEnabled(PhonePerceptionService.clipboardKey);
    final media = await PhonePerceptionService.subEnabled(PhonePerceptionService.mediaKey);
    final mediaFiles = await PhonePerceptionService.subEnabled(PhonePerceptionService.mediaFilesKey);
    final notif = await PhonePerceptionService.subEnabled(PhonePerceptionService.notificationKey);
    final autoNotif = await PhonePerceptionService.subEnabled(PhonePerceptionService.autoNotifyKey);
    final actions = await PhonePerceptionService.isActionsEnabled();
    final status = await PhonePerceptionService.getScreenStatus();
    final notifOk = await PhonePerceptionService.isNotificationAccessEnabled();
    final workflowBridge = await DeviceActionPrefs.isWorkflowBridgeEnabled();
    if (!mounted) return;
    setState(() {
      _enabled = prefsEnabled;
      _screenOn = screen;
      _clipboardOn = clip;
      _mediaOn = media;
      _mediaFilesOn = mediaFiles;
      _notificationOn = notif;
      _autoNotifyOn = autoNotif;
      _actionsOn = actions;
      _serviceEnabled = (status["serviceEnabled"] as bool? ?? false);
      _notifServiceEnabled = notifOk;
      _workflowBridgeOn = workflowBridge;
    });
    // R5/R4：健康检测 + 电池白名单
    _loadHealth();
  }

  Future<void> _loadHealth() async {
    final health = await PhonePerceptionService.getServiceHealth();
    final batteryOk = await PhonePerceptionService.isIgnoringBatteryOptimizations();
    if (mounted) {
      setState(() {
        _health = health;
        _batteryOk = batteryOk;
      });
    }
  }

  bool get _shizukuReady => _shizukuServer && _shizukuGranted;

  Future<void> _loadShizukuState() async {
    final st = await ShizukuService.status();
    if (mounted) {
      setState(() {
        _shizukuServer = st["serverRunning"] == true;
        _shizukuGranted = st["permissionGranted"] == true;
      });
    }
  }

  /// Shizuku 授权下采集系统状态（前台应用/屏幕/电池/网络/勿扰/设备）并上报 AI
  Future<void> _collectShizuku() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _shizukuBusy = true);
    final r = await ShizukuService.getSystemSnapshot();
    // P1 复核补（2026-09-22 真机反馈）：native 侧「8 条命令全失败」现在会回 ok=false，
    // 本按钮必须同样拦一道——此前它不看 ok，照样把空 data 格式化成
    // 「手机状态：屏幕熄灭；勿扰：关闭」上报，等于空壳仍旧写进感知历史。
    if (r["ok"] != true) {
      if (!mounted) return;
      setState(() {
        _shizukuBusy = false;
        _shizukuSnapshot = l10n.ppShizukuCollectFailed;
      });
      return;
    }
    final data = Map<String, dynamic>.from(r["data"] as Map? ?? {});
    final text = ShizukuService.formatSnapshot(data, isEn: await appLang() == "en");
    final ok = await PhonePerceptionService.uploadSnapshot(text, "shizuku_system");
    if (!mounted) return;
    setState(() {
      _shizukuBusy = false;
      _shizukuSnapshot = ok ? text : l10n.ppShizukuUploadFailed(text);
    });
  }

  Widget _shizukuDot(bool ok, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: ok ? AppColors.success : AppColors.separator,
          ),
        ),
        const SizedBox(width: 5),
        Text(label, style: const TextStyle(fontSize: 12, color: AppColors.textMuted)),
      ],
    );
  }

  Future<void> _toggleEnabled(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _enabled = v);
    await PhonePerceptionService.setEnabled(v);
    // 关闭总开关时同时停用全部子项，避免残留授权
    if (!v) {
      setState(() {
        _screenOn = false;
        _clipboardOn = false;
        _mediaOn = false;
        _mediaFilesOn = false;
        _notificationOn = false;
      });
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.screenKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.clipboardKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.notificationKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.autoNotifyKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
      _usageTimer?.cancel();
      setState(() {
        _autoNotifyOn = false;
        _usageStatsOn = false;
      });
    }
    _showSnack(v ? l10n.ppEnabledOn : l10n.ppEnabledOff);
  }

  Future<void> _toggleScreen(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      await PhonePerceptionService.openAccessibilitySettings();
      _showSnack(l10n.ppOpenAccessibility);
    }
    setState(() => _screenOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.screenKey, v);
    await _refreshServiceState();
  }

  Future<void> _loadUsageStatsState() async {
    final on = await PhonePerceptionService.subEnabled(PhonePerceptionService.usageStatsKey);
    final granted = await PhonePerceptionService.isUsageStatsEnabled();
    if (mounted) {
      setState(() {
        _usageStatsOn = on;
        _usageStatsGranted = granted;
      });
    }
    if (on) {
      _startUsageTimer();
    }
  }

  void _startUsageTimer() {
    _usageTimer?.cancel();
    _usageTimer = Timer.periodic(const Duration(minutes: 30), (_) {
      PhonePerceptionService.uploadUsageStats();
    });
  }

  /// 从系统授权页返回后调用：已授权则自动开启并上报
  Future<void> _autoEnableUsageStatsAfterReturn() async {
    final l10n = AppLocalizations.of(context)!;
    _pendingUsageGrant = false;
    if (!mounted || !_enabled || _usageStatsOn) return;
    final granted = await PhonePerceptionService.isUsageStatsEnabled();
    if (!granted || !mounted) {
      if (mounted) _showSnack(l10n.ppUsageNotGranted);
      return;
    }
    setState(() {
      _usageStatsOn = true;
      _usageStatsGranted = true;
    });
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, true);
    final content = await PhonePerceptionService.uploadUsageStats();
    _startUsageTimer();
    if (mounted) {
      _showSnack(content != null ? l10n.ppUsageGrantedWith(content) : l10n.ppUsageGrantedEmpty);
    }
  }

  Future<void> _toggleUsageStats(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.isUsageStatsEnabled();
      if (!granted) {
        // 系统「使用情况访问」特殊权限：引导跳转，返回时自动重查（_pendingUsageGrant）
        _pendingUsageGrant = true;
        await PhonePerceptionService.openUsageAccessSettings();
        _showSnack(l10n.ppUsageOpenSettings);
        if (mounted) setState(() => _usageStatsOn = false);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
        return;
      }
      setState(() {
        _usageStatsOn = true;
        _usageStatsGranted = true;
      });
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, true);
      final content = await PhonePerceptionService.uploadUsageStats();
      _showSnack(content != null ? l10n.ppUsageEnabledWith(content) : l10n.ppUsageEnabledEmpty);
      _startUsageTimer();
    } else {
      _usageTimer?.cancel();
      setState(() => _usageStatsOn = false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
      _showSnack(l10n.ppUsageDisabled);
    }
  }

  Future<void> _toggleNotification(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      await PhonePerceptionService.openNotificationSettings();
      _showSnack(l10n.ppOpenNotification);
    }
    setState(() => _notificationOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.notificationKey, v);
    final ok = await PhonePerceptionService.isNotificationAccessEnabled();
    if (mounted) setState(() => _notifServiceEnabled = ok);
  }

  Future<void> _toggleMedia(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.requestMediaPermission();
      if (!granted && mounted) {
        setState(() => _mediaOn = false);
        _showSnack(l10n.ppMediaDenied);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, false);
        await PhonePerceptionService.openAppSettings();
        return;
      }
    }
    setState(() => _mediaOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, v);
  }

  Future<void> _toggleMediaFiles(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.requestMediaFilesPermission();
      if (!granted && mounted) {
        setState(() => _mediaFilesOn = false);
        _showSnack(l10n.ppMediaFilesDenied);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, false);
        await PhonePerceptionService.openAppSettings();
        return;
      }
    }
    setState(() => _mediaFilesOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, v);
  }

  Future<void> _refreshServiceState() async {
    final status = await PhonePerceptionService.getScreenStatus();
    if (!mounted) return;
    setState(() => _serviceEnabled = (status["serviceEnabled"] as bool? ?? false));
  }

  Future<void> _loadHistory() async {
    setState(() => _historyLoading = true);
    final list = await PhonePerceptionService.fetchHistory();
    if (!mounted) return;
    setState(() {
      _history = list;
      _historyLoading = false;
    });
  }

  Future<void> _collectNow() async {
    final l10n = AppLocalizations.of(context)!;
    final r = await PhonePerceptionService.collectAndUpload();
    if (!mounted) return;
    final content = (r["content"] as String? ?? "").trim();
    final preview = content.length > 40 ? "${content.substring(0, 40)}..." : content;
    final msg = switch (r["status"]) {
      "ok" => l10n.ppCollectedWith(preview),
      "disabled" => l10n.ppCollectDisabled,
      "no_sources" => l10n.ppCollectNoSources,
      "empty" => l10n.ppCollectEmpty,
      "network_error" => l10n.ppCollectNetworkError,
      _ => l10n.ppCollectDone,
    };
    _showSnack(msg);
  }

  Future<void> _clearAll() async {
    final l10n = AppLocalizations.of(context)!;
    final r = await PhonePerceptionService.clearAll();
    final serverOk = r["serverOk"] == true;
    final localCleared = r["localCleared"] == true;
    _showSnack(serverOk ? l10n.ppClearedAll : l10n.ppClearedLocalOnly);
    // 本地待补传队列清掉了就先空掉列表：留着「服务端还没删掉」的旧快照只会让人以为
    // 本地也没清干净；服务端那部分联网后再点一次即可。
    if (serverOk || localCleared) {
      setState(() => _history = []);
    }
  }

  /// P4：导出诊断信息——感知日志 + 各通道健康状态拼成一段文本，弹窗展示可复制。
  /// 不写文件、不跳系统分享（零权限），排查时用户自己复制走。
  Future<void> _showDiagnostics() async {
    final l10n = AppLocalizations.of(context)!;
    // P2b：上下文预算节先取数再传入。取不到只让该节显示 unavailable(原因)，其余段落照旧导出。
    Map<String, dynamic>? budget;
    var budgetError = "";
    try {
      budget = await ApiClient().getContextBudget();
    } catch (e) {
      budgetError = e.toString();
    }
    final text = await PhonePerceptionService.buildDiagnosticsText(
      budget: budget,
      budgetError: budgetError,
    );
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.ppDiagnosticsTitle),
        content: SizedBox(
          width: double.maxFinite,
          height: 320,
          child: Scrollbar(
            child: SingleChildScrollView(
              child: SelectableText(
                text,
                style: const TextStyle(fontSize: 11, fontFamily: "monospace", height: 1.5),
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: text));
              if (!ctx.mounted) return;
              Navigator.pop(ctx);
              _showSnack(l10n.copied);
            },
            child: Text(l10n.copy),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text(l10n.close),
          ),
        ],
      ),
    );
  }


  /// M4b-2 自检：以 `dry_run=true` 提交一条意图，把服务端返回的 `reason` **原样**展示，
  /// 让用户一眼看出自己被哪层闸门挡住（后端干跑不发 token、不入队，链路上不会有动作）。
  Future<void> _selfCheckActionGate() async {
    final l10n = AppLocalizations.of(context)!;
    final r = await DeviceActionService.submitIntent(
      capability: DeviceActionExecutor.capOpenApp,
      targetApp: _selfActionTarget,
      dryRun: true,
    );
    if (!mounted) return;
    final line = r.allowed
        ? l10n.ppActionSelfCheckAllowed(r.status)
        : l10n.ppActionSelfCheckDenied(r.reason);
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.ppActionSelfCheckTitle),
        content: SelectableText(line, style: const TextStyle(fontSize: 12, height: 1.5)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close)),
        ],
      ),
    );
  }

  /// M4b-2 收尾：内置链路自证——先提交一条**真实**意图（本应用 open_app），
  /// 再复用「取待办 → 首次确认 → 执行 → 回报」那条路径。
  /// 这是 M4b 验收口径「App 内置入口可执行一条 open_app 并留下审计」的入口；上面那条是干跑自检。
  Future<void> _submitAndRunAction() async {
    final l10n = AppLocalizations.of(context)!;
    final granted = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.ppActionSubmitRun),
        content: Text(l10n.ppActionSubmitRunSub),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (granted != true || !mounted) return;
    final sub = await DeviceActionService.submitIntent(
      capability: DeviceActionExecutor.capOpenApp,
      targetApp: _selfActionTarget,
    );
    if (!mounted) return;
    if (!sub.allowed) {
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.ppActionSelfCheckTitle),
          content: SelectableText(l10n.ppActionSelfCheckDenied(sub.reason),
              style: const TextStyle(fontSize: 12, height: 1.5)),
          actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close))],
        ),
      );
      return;
    }
    await _runPendingActions();
  }

  /// M4c-5 三档确认策略设置：轻＝只在首次授权时确认一次（落 prefs）/ 中＝每类本次会话首次确认（默认）/
  /// 重＝每次执行都确认。只读写本机 prefs，不碰后端；执行侧统一由 `DeviceActionExecutor.runOnce` 按档位判定。
  Future<void> _showActionPolicySettings() async {
    final l10n = AppLocalizations.of(context)!;
    const caps = [
      DeviceActionExecutor.capOpenApp,
      DeviceActionExecutor.capTap,
      DeviceActionExecutor.capSetText,
    ];
    final capLabels = <String, String>{
      DeviceActionExecutor.capOpenApp: l10n.ppActionPolicyCapOpenApp,
      DeviceActionExecutor.capTap: l10n.ppActionPolicyCapTap,
      DeviceActionExecutor.capSetText: l10n.ppActionPolicyCapSetText,
    };
    final tierLabels = <ActionConfirmPolicy, String>{
      ActionConfirmPolicy.onceEver: l10n.ppActionPolicyOnceEver,
      ActionConfirmPolicy.firstPerType: l10n.ppActionPolicyFirstPerType,
      ActionConfirmPolicy.everyTime: l10n.ppActionPolicyEveryTime,
    };
    final selected = <String, ActionConfirmPolicy>{};
    for (final c in caps) {
      selected[c] = await DeviceActionPrefs.policyFor(c); // 读失败＝中档，不抛给 UI
    }
    if (!mounted) return;
    final save = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: Text(l10n.ppActionPolicyTitle),
          content: SizedBox(
            width: double.maxFinite,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 380),
              child: Scrollbar(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      for (final c in caps)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                capLabels[c]!,
                                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                              ),
                              RadioGroup<ActionConfirmPolicy>(
                                groupValue: selected[c],
                                onChanged: (v) {
                                  if (v != null) setDialogState(() => selected[c] = v);
                                },
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    for (final p in ActionConfirmPolicy.values)
                                      RadioListTile<ActionConfirmPolicy>(
                                        value: p,
                                        dense: true,
                                        contentPadding: EdgeInsets.zero,
                                        title: Text(
                                          tierLabels[p]!,
                                          style: const TextStyle(fontSize: 12, height: 1.4),
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
          ],
        ),
      ),
    );
    if (save != true) return;
    var allOk = true;
    for (final c in caps) {
      allOk = await DeviceActionPrefs.setPolicy(c, selected[c]!) && allOk;
    }
    if (!mounted) return;
    _showSnack(allOk ? l10n.ppActionPolicySaved : l10n.ppActionPolicySaveFailed);
  }

  /// M4b-2 执行待办：取回已批准的意图，逐类首次确认后在本机执行并回报（台账原样列出）。
  Future<void> _runPendingActions() async {
    final l10n = AppLocalizations.of(context)!;
    final rows = await DeviceActionExecutor.runOnce(
      confirmedTypes: _actionConfirmed,
      confirm: (capability) async {
        if (!mounted) return false;
        final granted = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.ppActionConfirmTitle),
            content: Text(l10n.ppActionConfirmBody(capability)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
              TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
            ],
          ),
        );
        return granted == true;
      },
    );
    if (!mounted) return;
    if (rows.isEmpty) {
      _showSnack(l10n.ppActionRunEmpty);
      return;
    }
    final text = rows
        .map((r) => "${r["action_token"]}  ${r["capability"]}  -> ${r["status"]}  ${r["detail"]}")
        .join("\n");
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.ppActionResultTitle),
        content: SizedBox(
          width: double.maxFinite,
          child: Scrollbar(
            child: SingleChildScrollView(
              child: SelectableText(
                text,
                style: const TextStyle(fontSize: 11, fontFamily: "monospace", height: 1.5),
              ),
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close)),
        ],
      ),
    );
  }

  /// M4d-3「工作流端口自检」：用**最小可映射工作流**真跑一遍新路径，让真机验证自服务。
  /// 序列固定为「打开本应用 → 点击本页一个真实可见的分组标题」两步：首步是 `launch_app`、
  /// 两步都属可映射类型、包名合法；点击目标是纯文本标题（不可点，点了既不跳页也不改设置）。
  /// 展示口径：走的通道 / 每步 step·action·target·ok·message / 端口返回的 reason **原样**单列一行；
  /// 映射不了或端口被拒都不粉饰成成功，也不回退本机路径去“凑个能看的结论”。
  Future<void> _runWorkflowPortSelfCheck() async {
    final l10n = AppLocalizations.of(context)!;
    // M4d-3 收尾（Codex，2026-09-23）：工作流端口开关**未开时一步都不执行**——否则自检会真的
    // 打开本应用并点一下标题（真实副作用），而它本意只是验证「新端口能不能用」。
    if (!await DeviceActionPrefs.isWorkflowBridgeEnabled()) {
      if (!mounted) return;
      await _showWorkflowSelfCheck(l10n, l10n.ppActionWfSelfCheckBridgeOff);
      return;
    }
    final steps = workflowSelfCheckSteps(l10n.ppGroupActions);
    final inspected = inspectWorkflow(steps);
    final plan = inspected.plan;
    if (plan == null) {
      // 连自检序列都映射不了＝这条路的构造前提没了：一步都不执行，直接把机器可读原因原样带出
      await _showWorkflowSelfCheck(l10n, l10n.ppActionWfSelfCheckNotMappable(inspected.reason));
      return;
    }
    final rows = await WorkflowActionBridge.withConfirmHandler(
      // 与「执行待办动作」同款确认弹窗；拿不到 context/文案一律 false（绝不默认放行）
      (capability) async {
        if (!mounted) return false;
        final granted = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.ppActionConfirmTitle),
            content: Text(l10n.ppActionConfirmBody(capability)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
              TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
            ],
          ),
        );
        return granted == true;
      },
      () => PhonePerceptionService.executeActionSequence(steps),
    );
    if (!mounted) return;
    final via = rows.isEmpty ? "" : (rows.first["via"] ?? "").toString();
    final lines = <String>[
      via == WorkflowActionBridge.viaPort
          ? l10n.ppActionWfSelfCheckViaPort
          : l10n.ppActionWfSelfCheckViaLegacy,
      if (via != WorkflowActionBridge.viaPort) l10n.ppActionWfSelfCheckLegacyNote,
      l10n.ppActionWfSelfCheckTarget(plan.targetApp),
      if (rows.isEmpty) l10n.ppActionWfSelfCheckEmpty,
      for (final r in rows)
        "${r["step"]}. ${r["action"]} [${r["target"]}] -> "
            "${r["ok"] == true ? l10n.ppActionWfSelfCheckOk : l10n.ppActionWfSelfCheckFail}: ${r["message"] ?? ""}",
    ];
    final denied = rows
        .where((r) => (r["via"] ?? "").toString() == WorkflowActionBridge.viaPort && r["ok"] != true)
        .toList();
    if (denied.isNotEmpty) {
      // 端口拒绝＝服务端 reason 原样，不解释、不翻译、不加包装
      lines.add(l10n.ppActionSelfCheckDenied((denied.first["message"] ?? "").toString()));
    }
    await _showWorkflowSelfCheck(l10n, lines.join("\n"));
  }

  Future<void> _showWorkflowSelfCheck(AppLocalizations l10n, String body) async {
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.ppActionWfSelfCheckTitle),
        content: SizedBox(
          width: double.maxFinite,
          child: Scrollbar(
            child: SingleChildScrollView(
              child: SelectableText(
                body,
                style: const TextStyle(fontSize: 11, fontFamily: "monospace", height: 1.5),
              ),
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close)),
        ],
      ),
    );
  }

  String get _userLocationDisplay {
    final l10n = AppLocalizations.of(context)!;
    if (_locationGpsEnabled) {
      if (_locationCity.isNotEmpty) return l10n.ppLocCityLocated(_locationCity);
      if (_locationLat != null && _locationLng != null) {
        return l10n.ppLocCoordsLocated(_locationLat!.toStringAsFixed(4), _locationLng!.toStringAsFixed(4));
      }
      return l10n.ppLocLocating;
    }
    return _userLocation.isEmpty ? l10n.ppLocUnset : _userLocation;
  }

  String get _aiLocationDisplay {
    final l10n = AppLocalizations.of(context)!;
    if (_locationFollow) {
      return l10n.ppLocFollowUser(_userLocation.isEmpty ? l10n.ppLocNotSet : _userLocation);
    }
    return _aiLocation.isEmpty ? l10n.ppLocUnset : _aiLocation;
  }

  String get _locationSubtitle {
    final l10n = AppLocalizations.of(context)!;
    final parts = <String>[];
    final uloc = _locationCity.isNotEmpty ? _locationCity : _userLocation;
    if (uloc.isNotEmpty) parts.add(l10n.ppLocUser(uloc));
    if (_aiLocation.isNotEmpty) parts.add(l10n.ppLocAi(_aiLocation));
    if (_locationFollow) parts.add(l10n.ppLocFollow);
    if (_locationGpsEnabled) parts.add(l10n.ppLocGpsOn);
    return parts.isEmpty ? l10n.ppLocUnsetExpand : parts.join(" · ");
  }

  /// 上报手机本地时区（分钟偏移，如 480=UTC+8），供角色时间感知；失败静默
  Future<void> _reportTimezone() async {
    try {
      final off = DateTime.now().timeZoneOffset.inMinutes;
      await ApiClient().updateUserLocation(timezoneOffsetMinutes: off);
    } catch (_) {}
  }

  Future<void> _loadLocation() async {
    try {
      final loc = await ApiClient().getUserLocation();
      if (!mounted) return;
      setState(() {
        _locationEnabled = loc["location_enabled"] == true;
        _locationGpsEnabled = loc["location_gps_enabled"] == true;
        _userLocation = (loc["user_location"] as String? ?? "").trim();
        _aiLocation = (loc["ai_location"] as String? ?? "").trim();
        _locationFollow = loc["location_follow"] == true;
        _locationLat = (loc["location_lat"] as num?)?.toDouble();
        _locationLng = (loc["location_lng"] as num?)?.toDouble();
        _locationCity = (loc["location_city"] as String? ?? "").trim();
      });
      // GPS 已开启但尚无坐标（旧版本开启的开关）：自动尝试定位一次
      if (_locationGpsEnabled && _locationLat == null && _locationLng == null) {
        _locateAndReport();
      }
    } catch (_) {}
  }

  Future<void> _toggleLocation(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _locationEnabled = v);
    await ApiClient().updateUserLocation(locationEnabled: v);
    if (!v) {
      setState(() {
        _locationGpsEnabled = false;
        _locationFollow = false;
        _userLocation = "";
        _aiLocation = "";
      });
    }
    _showSnack(v ? l10n.ppLocEnabledOn : l10n.ppLocEnabledOff);
  }

  Future<void> _toggleLocationGps(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final ok = await _locateAndReport();
      if (!ok) {
        setState(() => _locationGpsEnabled = false);
        await ApiClient().updateUserLocation(locationGpsEnabled: false);
        return;
      }
      setState(() => _locationGpsEnabled = true);
      _showSnack(l10n.ppLocGpsEnabledWith(_userLocationDisplay));
    } else {
      setState(() => _locationGpsEnabled = false);
      await ApiClient().updateUserLocation(locationGpsEnabled: false);
      _showSnack(l10n.ppLocGpsDisabled);
    }
  }

  /// 请求定位权限 → 获取经纬度 → 上报后端（后端反查城市名）；成功 true
  Future<bool> _locateAndReport() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final enabled = await Geolocator.isLocationServiceEnabled();
      if (!enabled) {
        _showSnack(l10n.ppLocServiceOff);
        return false;
      }
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
        _showSnack(perm == LocationPermission.deniedForever
            ? l10n.ppLocDeniedForever
            : l10n.ppLocNoPermission);
        return false;
      }
      // 优先 Android 原生 LocationManager（GPS+网络基站定位，不依赖 Google 定位服务），
      // 避免国行机型无 Google 网络时 FusedLocationProvider 超时导致定位失败
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: AndroidSettings(
          accuracy: LocationAccuracy.medium,
          timeLimit: Duration(seconds: 20),
          forceLocationManager: true,
        ),
      );
      await ApiClient().updateUserLocation(
        locationGpsEnabled: true,
        locationLat: pos.latitude,
        locationLng: pos.longitude,
      );
      await _loadLocation();
      return true;
    } catch (e) {
      _showSnack(l10n.ppLocFailed('$e'));
      return false;
    }
  }

  Future<void> _toggleLocationFollow(bool v) async {
    setState(() {
      _locationFollow = v;
      if (v) _aiLocation = _userLocation; // 位置跟随：AI 位置与用户相同
    });
    await ApiClient().updateUserLocation(
      locationFollow: v,
      aiLocation: v ? _userLocation : _aiLocation,
    );
  }

  Future<void> _editLocation({required bool isUser}) async {
    final l10n = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: isUser ? _userLocation : _aiLocation);
    try {
      final value = await showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(isUser ? l10n.ppLocSetUser : l10n.ppLocSetAi),
          content: TextField(
            controller: controller,
            maxLength: 50,
            decoration: InputDecoration(hintText: l10n.ppLocHint, counterText: ""),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
            TextButton(
              onPressed: () => Navigator.pop(ctx, controller.text.trim()),
              child: Text(l10n.save),
            ),
          ],
        ),
      );
      if (value == null || value.isEmpty) return;
      if (isUser) {
        setState(() {
          _userLocation = value;
          if (_locationFollow) _aiLocation = value;
        });
        await ApiClient().updateUserLocation(
          userLocation: value,
          aiLocation: _locationFollow ? value : null,
        );
      } else {
        setState(() => _aiLocation = value);
        await ApiClient().updateUserLocation(aiLocation: value);
      }
    } finally {
      controller.dispose();
    }
  }

  void _showSnack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  String _shortTime(String raw) {
    final r = formatBeijingTime(raw).replaceAll("T", " ").trim();
    return r.length >= 16 ? r.substring(0, 16) : r;
  }

  String _sourceLabel(String s) {
    final l10n = AppLocalizations.of(context)!;
    switch (s) {
      case "accessibility":
        return l10n.ppSourceScreen;
      case "clipboard":
        return l10n.ppSourceClipboard;
      case "media":
        return l10n.ppSourceMedia;
      case "notification":
        return l10n.ppSourceNotification;
      default:
        return s;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    const subColor = AppColors.textSecondary;

    return Scaffold(
      appBar: AppBar(title: Text(l10n.phonePerception)),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 24),
        children: [
          // 总开关
          PpGroup(title: null, children: [
            PpSwitch(
              icon: Icons.visibility_outlined,
              title: l10n.phonePerception,
              subtitle: _enabled ? l10n.ppSubtitleOn : l10n.ppSubtitleOff,
              value: _enabled,
              onChanged: _toggleEnabled,
            ),
          ]),
          // R5：服务健康状态灯
          if (_health.isNotEmpty)
            PpGroup(title: l10n.healthRunningStatus, children: [
              PpHealthTile(icon: Icons.accessibility_new, title: l10n.healthAccessibility,
                      ok: _health['accessible'] == true,
                  sub: _health['accessibleInstanceAlive'] == true ? l10n.connected : l10n.healthAccessibilityNotConnected),
              const PpDivider(),
              PpHealthTile(icon: Icons.notifications_outlined, title: l10n.healthNotificationAccess,
                      ok: _health['notification'] == true,
                  sub: _health['notificationConnected'] == true ? l10n.connected : l10n.healthNotificationNotConnected),
              const PpDivider(),
              PpHealthTile(icon: Icons.shield_outlined, title: 'Shizuku',
                      ok: _health['shizuku'] == true,
                  sub: _health['shizukuRunning'] == true
                      ? (_health['shizukuGranted'] == true ? l10n.healthShizukuAuthorized : l10n.healthShizukuUnauthorized)
                      : l10n.healthShizukuNotRunning),
              const PpDivider(),
              PpHealthTile(icon: Icons.bar_chart, title: l10n.healthUsageAccess,
                      ok: _health['usageStats'] == true),
              const PpDivider(),
              PpHealthTile(icon: Icons.battery_saver, title: l10n.healthBatteryWhitelist,
                      ok: _batteryOk,
                  sub: _batteryOk ? l10n.healthBatteryAdded : l10n.healthBatteryNotAdded,
                  onTap: _batteryOk ? null : () async {
                    await PhonePerceptionService.requestIgnoreBatteryOptimizations();
                    Future.delayed(const Duration(seconds: 2), _loadHealth);
                  }),
            ]),
          // 采集项
          PpGroup(title: l10n.ppGroupSources, children: [
            PpFoldParent(
              icon: Icons.screen_share_outlined,
              title: l10n.ppScreenTitle,
              subtitle: _serviceEnabled ? l10n.ppScreenRunning : l10n.ppScreenOff,
              value: _screenOn,
              onChanged: _enabled ? _toggleScreen : null,
              expanded: _expandScreen,
              onToggle: () => setState(() => _expandScreen = !_expandScreen),
            ),
            if (_expandScreen) ...[
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.content_paste,
                  title: l10n.ppClipboard,
                  subtitle: l10n.ppClipboardSub,
                  value: _clipboardOn,
                  onChanged: _enabled
                      ? (v) async {
                          setState(() => _clipboardOn = v);
                          await PhonePerceptionService.setSubEnabled(PhonePerceptionService.clipboardKey, v);
                        }
                      : null,
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.photo_library_outlined,
                  title: l10n.ppMediaTitle,
                  subtitle: l10n.ppMediaSub,
                  value: _mediaOn,
                  onChanged: _enabled ? _toggleMedia : null,
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.video_library_outlined,
                  title: l10n.ppMediaFilesTitle,
                  subtitle: l10n.ppMediaFilesSub,
                  value: _mediaFilesOn,
                  onChanged: _enabled ? _toggleMediaFiles : null,
                ),
              ),
            ],
            const PpDivider(),
            PpSwitch(
              icon: Icons.bar_chart_outlined,
              title: l10n.ppUsageStatsTitle,
              subtitle: _usageStatsGranted
                  ? l10n.ppUsageStatsGranted
                  : l10n.ppUsageStatsNotGranted,
              value: _usageStatsOn,
              onChanged: _enabled ? _toggleUsageStats : null,
            ),
            const PpDivider(),
            PpFoldParent(
              icon: Icons.touch_app_outlined,
              title: l10n.ppActionsTitle,
              subtitle: _actionsOn
                  ? l10n.ppActionsOn
                  : l10n.ppActionsOff,
              value: _actionsOn,
              onChanged: _enabled && _screenOn
                  ? (v) async {
                      setState(() => _actionsOn = v);
                      await PhonePerceptionService.setActionsEnabled(v);
                    }
                  : null,
              expanded: _expandActions,
              onToggle: () => setState(() => _expandActions = !_expandActions),
            ),
            if (_expandActions) ...[
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpNav(
                  icon: Icons.account_tree_outlined,
                  title: l10n.ppWorkflowTitle,
                  subtitle: l10n.ppWorkflowSub,
                  enabled: _enabled,
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => const WorkflowScreen(),
                    ));
                  },
                ),
              ),
            ],
            const PpDivider(),
            PpFoldParent(
              icon: Icons.notifications_outlined,
              title: l10n.ppNotificationTitle,
              subtitle: _notifServiceEnabled
                  ? l10n.ppNotifRunning
                  : l10n.ppNotifOff,
              value: _notificationOn,
              onChanged: _enabled ? _toggleNotification : null,
              expanded: _expandNotif,
              onToggle: () => setState(() => _expandNotif = !_expandNotif),
            ),
            if (_expandNotif) ...[
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.auto_awesome_outlined,
                  title: l10n.ppAutoNotifyTitle,
                  subtitle: l10n.ppAutoNotifySub,
                  value: _autoNotifyOn,
                  onChanged: _enabled && _notificationOn
                      ? (v) async {
                          setState(() => _autoNotifyOn = v);
                          await PhonePerceptionService.setSubEnabled(PhonePerceptionService.autoNotifyKey, v);
                        }
                      : null,
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpNav(
                  icon: Icons.notifications_none_outlined,
                  title: l10n.ppWhitelistTitle,
                  subtitle: l10n.ppWhitelistSub,
                  enabled: _enabled && _notificationOn,
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => const NotificationWhitelistScreen()),
                    );
                  },
                ),
              ),
            ],
            const PpDivider(),
            PpNav(
              icon: Icons.security_outlined,
              title: l10n.ppShizukuTitle,
              subtitle: l10n.ppShizukuSub,
              enabled: _enabled,
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const ShizukuScreen()),
              ),
            ),
            const PpDivider(),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 4, 14, 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      _shizukuDot(_shizukuServer, l10n.ppShizukuServer),
                      const SizedBox(width: 14),
                      _shizukuDot(_shizukuGranted, l10n.ppShizukuGranted),
                      const Spacer(),
                      Text(
                        _shizukuReady ? l10n.ppReady : l10n.ppNotReady,
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                          color: _shizukuReady ? AppColors.success : subColor,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _enabled && _shizukuReady && !_shizukuBusy
                          ? _collectShizuku
                          : null,
                      icon: const Icon(Icons.sensors, size: 18),
                      label: Text(_shizukuBusy ? l10n.ppCollecting : l10n.ppCollectShizuku),
                    ),
                  ),
                  if (_shizukuSnapshot.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        _shizukuSnapshot,
                        style: const TextStyle(fontSize: 12, color: subColor, height: 1.5),
                      ),
                    ),
                ],
              ),
            ),
          ]),
          // 位置
          PpGroup(title: l10n.ppGroupLocation, children: [
            PpFoldParent(
              icon: Icons.location_on_outlined,
              title: l10n.ppLocationTitle,
              subtitle: _locationEnabled
                  ? (_locationSubtitle.isNotEmpty
                      ? _locationSubtitle
                      : l10n.ppLocSubtitleOn)
                  : l10n.ppLocSubtitleOff,
              value: _locationEnabled,
              onChanged: _toggleLocation,
              expanded: _expandLocation,
              onToggle: () => setState(() => _expandLocation = !_expandLocation),
            ),
            if (_expandLocation) ...[
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.gps_fixed,
                  title: l10n.ppLocGpsTitle,
                  subtitle: _locationGpsEnabled ? l10n.ppLocGpsOnSub : l10n.ppLocGpsOffSub,
                  value: _locationGpsEnabled,
                  onChanged: _locationEnabled ? _toggleLocationGps : null,
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpNav(
                  icon: Icons.person_pin_circle_outlined,
                  title: l10n.ppLocUserTitle,
                  subtitle: _userLocationDisplay,
                  enabled: _locationEnabled && !_locationGpsEnabled,
                  trailing: const Icon(Icons.edit_outlined, size: 20, color: AppColors.separator),
                  onTap: () => _editLocation(isUser: true),
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpNav(
                  icon: Icons.smart_toy_outlined,
                  title: l10n.ppLocAiTitle,
                  subtitle: _aiLocationDisplay,
                  enabled: _locationEnabled && !_locationFollow,
                  trailing: const Icon(Icons.edit_outlined, size: 20, color: AppColors.separator),
                  onTap: () => _editLocation(isUser: false),
                ),
              ),
              const PpDivider(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: PpSwitch(
                  icon: Icons.sync_alt,
                  title: l10n.ppLocFollowTitle,
                  subtitle: _locationFollow ? l10n.ppLocFollowOnSub : l10n.ppLocFollowOffSub,
                  value: _locationFollow,
                  onChanged: _locationEnabled ? _toggleLocationFollow : null,
                ),
              ),
            ],
          ]),
          // 隐私说明
          PpGroup(title: l10n.ppGroupPrivacy, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
              child: Text(
                l10n.ppPrivacyNote,
                style: const TextStyle(fontSize: 12, color: AppColors.textMuted, height: 1.6),
              ),
            ),
            const PpDivider(),
            // P4：通道健康在真机上没法观察（此前「导出感知日志」在 Dart 侧没有任何入口）
            PpNav(
              icon: Icons.bug_report_outlined,
              title: l10n.ppDiagnosticsTitle,
              subtitle: l10n.ppDiagnosticsSub,
              onTap: _showDiagnostics,
            ),
            // M4b-2：内置行动通道（后端批准 → 本机执行 → 回报）的两个入口
            const PpDivider(),
            PpNav(
              icon: Icons.verified_outlined,
              title: l10n.ppActionSelfCheck,
              subtitle: l10n.ppActionSelfCheckSub,
              onTap: _selfCheckActionGate,
            ),
            const PpDivider(),
            PpNav(
              icon: Icons.play_circle_outline,
              title: l10n.ppActionRunPending,
              subtitle: l10n.ppActionRunPendingSub,
              onTap: _runPendingActions,
            ),
            const PpDivider(),
            PpNav(
              icon: Icons.open_in_new,
              title: l10n.ppActionSubmitRun,
              subtitle: l10n.ppActionSubmitRunSub,
              onTap: _submitAndRunAction,
            ),
            // M4c-5：三档确认策略入口（轻/中/重逐类选择，只落本机 prefs）
            const PpDivider(),
            PpNav(
              icon: Icons.rule_outlined,
              title: l10n.ppActionPolicy,
              subtitle: l10n.ppActionPolicySub,
              onTap: _showActionPolicySettings,
            ),
            // M4d：工作流改走行动端口的客户端开关（缺省＝关＝旧路径逐字不变）
            const PpDivider(),
            PpSwitch(
              icon: Icons.alt_route,
              title: l10n.ppActionWorkflowBridge,
              subtitle: l10n.ppActionWorkflowBridgeSub,
              value: _workflowBridgeOn,
              onChanged: (v) async {
                setState(() => _workflowBridgeOn = v);
                await DeviceActionPrefs.setWorkflowBridgeEnabled(v);
              },
            ),
            // M4d-3：新路径一键自检（真机验证自服务：走没走端口、被拒的原因原样摊开）
            const PpDivider(),
            PpNav(
              icon: Icons.route_outlined,
              title: l10n.ppActionWfSelfCheck,
              subtitle: l10n.ppActionWfSelfCheckSub,
              onTap: _runWorkflowPortSelfCheck,
            ),
          ]),
          // 操作与记录
          PpGroup(title: l10n.ppGroupActions, children: [
            PpNav(
              icon: Icons.my_location,
              title: l10n.ppCollectNowTitle,
              subtitle: l10n.ppCollectNowSub,
              color: AppColors.success,
              onTap: _collectNow,
            ),
            const PpDivider(),
            PpNav(
              icon: Icons.history,
              title: l10n.ppHistoryTitle,
              subtitle: _history.isEmpty ? l10n.ppNoSnapshots : l10n.ppRecentCount('${_history.length}'),
              trailing: _historyLoading
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
              onTap: () {
                setState(() => _showHistory = !_showHistory);
                // 本页是用户自己的系统级感知，不做隐私上锁（2026-09-22 用户拍板）：展开即加载
                if (_showHistory) _loadHistory();
              },
            ),
            if (_showHistory)
              for (final s in _history)
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.phone_android, size: 18, color: subColor),
                  title: Text(
                    s["content"] ?? "",
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12),
                  ),
                  subtitle: Text(
                    "${_sourceLabel(s["source"] ?? "")} · ${_shortTime(s["created_at"]?.toString() ?? "")}",
                    style: const TextStyle(fontSize: 11, color: subColor),
                  ),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 14),
                ),
            if (_history.isNotEmpty || _showHistory) const PpDivider(),
            ListTile(
              leading: const Icon(Icons.delete_outline, color: AppColors.error),
              title: Text(l10n.ppClearAll, style: const TextStyle(fontSize: 15, color: AppColors.error)),
              contentPadding: const EdgeInsets.symmetric(horizontal: 14),
              onTap: _clearAll,
            ),
          ]),
        ],
      ),
    );
  }
}
