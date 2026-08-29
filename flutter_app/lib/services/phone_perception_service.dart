import "dart:convert" as dart_convert;
import "dart:io";
import "package:dio/dio.dart";
import "package:flutter/services.dart";
import "package:shared_preferences/shared_preferences.dart";
import "api_client.dart";
import "shizuku_service.dart";

/// 工作流触发词（2026-08-14 P1：帮我执行/跑一下 XX）
const List<String> kWorkflowTriggers = ["帮我执行", "帮我运行", "跑一下", "执行一下", "执行工作流", "工作流", "帮我跑"];

/// 手机感知（AI 走出沙箱 Phase 1）：读取屏幕文字/剪贴板/相册最近列表 → 上传服务器 → 注入聊天上下文。
/// 全部能力默认关闭，需在设置页逐项授权；数据只发自家服务器。
class PhonePerceptionService {
  static const MethodChannel _channel = MethodChannel("com.aicompanion/phone_perception");

  static const String enabledKey = "phone_perception_enabled";
  static const String screenKey = "pp_screen_enabled";
  static const String clipboardKey = "pp_clipboard_enabled";
  static const String mediaKey = "pp_media_enabled";
  static const String mediaFilesKey = "pp_media_files_enabled";
  static const String notificationKey = "pp_notification_enabled";
  static const String notifWhitelistKey = "pp_notif_whitelist";
  static const String autoNotifyKey = "pp_auto_notify";
  static const String actionsKey = "pp_actions_enabled";
  static const String usageStatsKey = "pp_usage_stats_enabled";
  /// 模拟操作由设置页开关（pp_actions_enabled）控制；2026-08-14 恢复双通道执行（无障碍 + Shizuku）

  // === 开关持久化 ===
  static Future<bool> isEnabled() async =>
      (await SharedPreferences.getInstance()).getBool(enabledKey) ?? false;

  static Future<void> setEnabled(bool v) async {
    final p = await SharedPreferences.getInstance();
    await p.setBool(enabledKey, v);
  }

  static Future<bool> subEnabled(String key) async =>
      (await SharedPreferences.getInstance()).getBool(key) ?? false;

  static Future<void> setSubEnabled(String key, bool v) async {
    final p = await SharedPreferences.getInstance();
    await p.setBool(key, v);
  }

  // === 原生通道 ===
  /// 读屏：返回 {text, capturedAt, serviceEnabled}
  static Future<Map<dynamic, dynamic>> getScreenStatus() async {
    if (!Platform.isAndroid) return {};
    try {
      final r = await _channel.invokeMethod("getScreenText") as Map? ?? {};
      return r;
    } catch (_) {
      return {};
    }
  }

  // “允许1分钟”临时放行：该时间段内执行操作不再弹授权
  static DateTime? autoAllowUntil;
  static bool get autoAllowed =>
      autoAllowUntil != null && DateTime.now().isBefore(autoAllowUntil!);
  static void allowForMinute() {
    autoAllowUntil = DateTime.now().add(const Duration(minutes: 1));
  }

  // === Phase 3：模拟操作（默认关闭，需在设置页单独授权） ===
  static Future<bool> isActionsEnabled() async =>
      (await SharedPreferences.getInstance()).getBool(actionsKey) ?? false;

  static Future<void> setActionsEnabled(bool v) async {
    final p = await SharedPreferences.getInstance();
    await p.setBool(actionsKey, v);
  }

  // === 应用使用时长（P0，2026-08-08）：UsageStatsManager 最近 24h 前台时长 ===
  static Future<bool> isUsageStatsEnabled() async {
    if (!Platform.isAndroid) return false;
    try {
      final r = await _channel.invokeMethod("getUsageStatsEnabled") as bool? ?? false;
      return r;
    } catch (_) {
      return false;
    }
  }

  static Future<void> openUsageAccessSettings() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod("openUsageAccessSettings");
    } catch (_) {}
  }

  /// 查询最近 24h 使用时长，返回 [{package, app_name, total_ms}]（按时长降序）
  static Future<List<Map<String, dynamic>>> getUsageStats({int top = 8}) async {
    if (!Platform.isAndroid) return [];
    try {
      final r = await _channel.invokeMethod("getUsageStats", {"top": top}) as List? ?? [];
      return r.cast<Map<String, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  static String _fmtDuration(int ms) {
    final totalMin = (ms / 60000).round();
    if (totalMin < 1) return "不足1分钟";
    final h = totalMin ~/ 60;
    final m = totalMin % 60;
    if (h == 0) return "$m分钟";
    if (m == 0) return "$h小时";
    return "$h小时$m分钟";
  }

  /// 查询并上报使用时长快照到服务器（source=usage_stats）
  static Future<String?> uploadUsageStats({int top = 8}) async {
    if (!Platform.isAndroid) return null;
    final items = await getUsageStats(top: top);
    if (items.isEmpty) return null;
    final parts = items
        .map((e) => "${e["app_name"]} ${_fmtDuration((e["total_ms"] as num?)?.toInt() ?? 0)}")
        .toList();
    final content = "最近24小时使用：${parts.join("、")}";
    try {
      final form = FormData.fromMap({
        "source": "usage_stats",
        "content": content,
      });
      await ApiClient().dio.post("/api/v1/phone/perception", data: form);
      return content;
    } catch (_) {
      return null;
    }
  }

  /// 上传任意文本快照到服务器（source 需在服务端白名单内）
  static Future<bool> uploadSnapshot(String content, String source) async {
    if (content.trim().isEmpty) return false;
    try {
      final form = FormData.fromMap({
        "source": source,
        "content": content,
      });
      await ApiClient().dio.post("/api/v1/phone/perception", data: form);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 查岗采集（2026-08-15）：Shizuku 可用时采集系统快照（含前台应用）并上报；失败静默
  static Future<bool> uploadShizukuSnapshotIfAvailable() async {
    try {
      final r = await ShizukuService.getSystemSnapshot();
      final data = Map<String, dynamic>.from(r["data"] as Map? ?? {});
      final text = ShizukuService.formatSnapshot(data);
      if (text.isEmpty) return false;
      return await uploadSnapshot(text, "shizuku_system");
    } catch (_) {
      return false;
    }
  }

  /// 节点树快照：返回 {text, nodes: [{text, clickable, editable, x, y}], capturedAt, serviceEnabled}
  static Future<Map<dynamic, dynamic>> getNodeTree() async {
    if (!Platform.isAndroid) return {};
    try {
      final r = await _channel.invokeMethod("getNodeTree") as Map? ?? {};
      return r;
    } catch (_) {
      return {};
    }
  }

  /// 执行单步动作：action ∈ click/long_click/scroll；target 必须来自当前节点树
  static Future<Map<dynamic, dynamic>> performAction(String action, String target) async {
    if (!Platform.isAndroid) return {"ok": false, "message": "非 Android"};
    try {
      final r = await _channel.invokeMethod(
        "performAction",
        {"action": action, "target": target},
      ) as Map? ?? {"ok": false, "message": "执行失败"};
      return r;
    } catch (_) {
      return {"ok": false, "message": "执行通道异常"};
    }
  }

  /// 输入文本到当前聚焦输入框（≤50 字）
  static Future<Map<dynamic, dynamic>> setTextOnFocus(String text) async {
    if (!Platform.isAndroid) return {"ok": false, "message": "非 Android"};
    try {
      final r = await _channel.invokeMethod("setText", {"text": text}) as Map? ?? {"ok": false, "message": "输入失败"};
      return r;
    } catch (_) {
      return {"ok": false, "message": "输入通道异常"};
    }
  }

  /// 把操作结果作为快照上传（source=action_result），供聊天上下文引用与动作日志落库
  static Future<bool> uploadActionResult(String action, String target, bool ok, String message) async {
    try {
      final dio = ApiClient().dio;
      final label = switch (action) {
        "click" => "点击",
        "long_click" => "长按",
        "scroll" => "滚动",
        "set_text" => "输入",
        _ => action,
      };
      final status = ok ? "成功" : "失败";
      await dio.post(
        "/api/v1/phone/perception",
        data: FormData.fromMap({
          "source": "action_result",
          "content": "操作[$label]“$target”→$status${ok ? "" : "（$message）"}",
        }),
      );
      return true;
    } catch (_) {
      return false;
    }
  }


  /// 通知：返回 [{app, package, title, text, time}]（最近 MAX_KEEP 条）
  static Future<List<Map<String, dynamic>>> getNotifications() async {
    if (!Platform.isAndroid) return [];
    try {
      final r = await _channel.invokeMethod("getNotifications") as List? ?? [];
      return r.cast<Map<dynamic, dynamic>>().map((m) => Map<String, dynamic>.from(m)).toList();
    } catch (_) {
      return [];
    }
  }

  static Future<bool> isNotificationAccessEnabled() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _channel.invokeMethod("isNotificationAccessEnabled") as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 读取 Kotlin 通知服务写入的缓存（后台 isolate 无 MethodChannel，只能读 prefs）
  static Future<List<Map<String, String>>> readCachedNotifications() async {
    final p = await SharedPreferences.getInstance();
    final raw = p.getString("notif_cache_json") ?? "";
    if (raw.isEmpty) return [];
    try {
      final arr = (dart_convert.jsonDecode(raw) as List? ?? []);
      return arr.map((e) => Map<String, String>.from(e as Map)).toList();
    } catch (_) {
      return [];
    }
  }

  /// 通知白名单：空 = 全部允许；非空 = 只感知勾选的 app（包名集合）
  static Future<Set<String>> getNotificationWhitelist() async {
    final p = await SharedPreferences.getInstance();
    return (p.getStringList(notifWhitelistKey) ?? []).toSet();
  }

  static Future<void> setNotificationWhitelist(Set<String> pkgs) async {
    final p = await SharedPreferences.getInstance();
    await p.setStringList(notifWhitelistKey, pkgs.toList());
  }

  static Future<void> openNotificationSettings() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod("openNotificationSettings");
    } catch (_) {}
  }

  static Future<bool> requestMediaFilesPermission() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _channel.invokeMethod("requestMediaFilesPermission") as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> openAllFilesAccessSettings() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod("openAllFilesAccessSettings");
    } catch (_) {}
  }

  static Future<bool> requestMediaPermission() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _channel.invokeMethod("requestMediaPermission") as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 屏幕物理像素尺寸（工作流坐标点击范围提示）→ {width, height}
  static Future<Map<String, dynamic>> getScreenSize() async {
    if (!Platform.isAndroid) return {'width': 0, 'height': 0};
    try {
      final r = await _channel.invokeMethod('getScreenSize') as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (_) {
      return {'width': 0, 'height': 0};
    }
  }

  /// R5：统一健康检测 → 各服务运行状态
  static Future<Map<String, dynamic>> getServiceHealth() async {
    if (!Platform.isAndroid) return {};
    try {
      final r = await _channel.invokeMethod('getServiceHealth') as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (_) {
      return {};
    }
  }

  /// R4：是否已加入电池优化白名单
  static Future<bool> isIgnoringBatteryOptimizations() async {
    if (!Platform.isAndroid) return true;
    try {
      return await _channel.invokeMethod('isIgnoringBatteryOptimizations') as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  /// R4：请求加入电池优化白名单
  static Future<bool> requestIgnoreBatteryOptimizations() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _channel.invokeMethod('requestIgnoreBatteryOptimizations') as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  /// R11：导出感知日志
  static Future<Map<String, dynamic>> exportPerceptionLog() async {
    if (!Platform.isAndroid) return {'ok': false, 'error': 'not android'};
    try {
      final r = await _channel.invokeMethod('exportPerceptionLog') as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  static Future<void> openAccessibilitySettings() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod("openAccessibilitySettings");
    } catch (_) {}
  }

  static Future<void> openAppSettings() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod("openAppSettings");
    } catch (_) {}
  }

  // === 采集并上传 ===
  /// 按开关采集屏幕/剪贴板/相册，分别 POST 到服务器。
  /// 返回：{status: disabled|no_sources|empty|ok|network_error, content?}
  static Future<Map<String, dynamic>> collectAndUpload() async {
    final prefs = await SharedPreferences.getInstance();
    if (!(prefs.getBool(enabledKey) ?? false)) return {"status": "disabled"};
    final screenOn = prefs.getBool(screenKey) ?? false;
    final clipOn = prefs.getBool(clipboardKey) ?? false;
    final mediaOn = prefs.getBool(mediaKey) ?? false;
    final mediaFilesOn = prefs.getBool(mediaFilesKey) ?? false;
    final notifOn = prefs.getBool(notificationKey) ?? false;
    if (!screenOn && !clipOn && !mediaOn && !mediaFilesOn && !notifOn) {
      return {"status": "no_sources"};
    }

    final uploads = <Map<String, String>>[];
    if (screenOn) {
      final s = await getScreenStatus();
      final text = (s["text"] as String? ?? "").trim();
      if (text.isNotEmpty) uploads.add({"source": "accessibility", "content": text});
    }
    if (clipOn) {
      final clip = await Clipboard.getData(Clipboard.kTextPlain);
      final t = clip?.text?.trim() ?? "";
      if (t.isNotEmpty) uploads.add({"source": "clipboard", "content": t});
    }
    if (mediaOn) {
      final photos = await getRecentPhotos(limit: 8);
      if (photos.isNotEmpty) {
        final lines = photos
            .map((p) => "${p["name"]}（${p["date"]}）")
            .join("、");
        uploads.add({"source": "media", "content": "最近相册：$lines"});
      }
    }
    if (mediaFilesOn) {
      for (final t in ["video", "audio", "document"]) {
        final files = await getRecentMediaFiles(type: t, limit: 5);
        if (files.isNotEmpty) {
          final label = t == "video" ? "最近视频" : (t == "audio" ? "最近音频" : "最近文档");
          final lines = files.map((f) => "${f["name"]}（${f["date"]}）").join("、");
          uploads.add({"source": "media_$t", "content": "$label：$lines"});
        }
      }
    }
    if (notifOn) {
      final notifs = await getNotifications();
      if (notifs.isNotEmpty) {
        final lines = notifs.take(5).map((n) {
          final t = (n["title"] ?? "").trim();
          final x = (n["text"] ?? "").trim();
          final body = [t, x].where((e) => e.isNotEmpty).join("：");
          return "${n["app"] ?? "通知"}：$body";
        }).join("；");
        uploads.add({"source": "notification", "content": "最近通知：$lines"});
      }
    }
    if (uploads.isEmpty) return {"status": "empty"};

    final dio = ApiClient().dio;
    var okCount = 0;
    for (final u in uploads) {
      try {
        await dio.post(
          "/api/v1/phone/perception",
          data: FormData.fromMap({"source": u["source"], "content": u["content"]}),
        );
        okCount++;
      } catch (_) {}
    }
    if (okCount == 0) return {"status": "network_error"};
    final total = uploads.map((u) => u["content"]).join("\n");
    return {"status": "ok", "content": total};
  }

  static Future<List<Map<dynamic, dynamic>>> getRecentPhotos({int limit = 8}) async {
    if (!Platform.isAndroid) return [];
    try {
      final r = await _channel.invokeMethod("getRecentPhotos", {"limit": limit}) as List? ?? [];
      return r.cast<Map<dynamic, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  static Future<List<Map<dynamic, dynamic>>> getRecentMediaFiles({
    String type = "video",
    int limit = 8,
  }) async {
    if (!Platform.isAndroid) return [];
    try {
      final r = await _channel
          .invokeMethod("getRecentMediaFiles", {"type": type, "limit": limit}) as List? ?? [];
      return r.cast<Map<dynamic, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  // === 历史记录 ===
  static Future<List<Map<String, dynamic>>> fetchHistory() async {
    try {
      final dio = ApiClient().dio;
      final r = await dio.get("/api/v1/phone/perception/recent");
      return ((r.data as Map<String, dynamic>)["snapshots"] as List? ?? [])
          .map((j) => j as Map<String, dynamic>)
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<bool> clearAll() async {
    try {
      final dio = ApiClient().dio;
      await dio.delete("/api/v1/phone/perception");
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 聊天触发词检测：用户在问/提及 AI 感知手机时，先采集上传再发送
  static bool hasPerceptionIntent(String text) {
    final t = text.trim();
    const patterns = [
      "你在干嘛", "你在干什么", "你看到", "你现在看到", "你看我", "我刚刚复制",
      "我刚复制", "我最近在看", "我在看", "我现在在", "我在做什么", "我手机",
      "我刚在", "我刚刚在", "你知道我", "看得到", "看到什么", "我屏幕",
      "你猜我在", "我在刷", "我刚复制", "刚刚复制", "我复制了",
      "谁给我发", "谁找我", "我手机通知", "通知", "谁发消息", "谁找我聊天",
      "我收到", "有人给我发", "新消息",
    ];
    return patterns.any(t.contains);
  }

  /// P1：从聊天文本匹配用户自建工作流（“帮我执行 XX / 跑一下 XX”）；未命中返回 null
  static Future<Map<String, dynamic>?> matchWorkflow(String text) async {
    try {
      final data = await ApiClient().listWorkflows();
      final items = (data['items'] as List? ?? []).cast<Map<String, dynamic>>();
      if (items.isEmpty) return null;
      var rest = text;
      for (final t in kWorkflowTriggers) {
        rest = rest.replaceAll(t, '');
      }
      rest = rest.replaceAll(RegExp('[“”"\'，。！？!?、\\s]'), '');
      if (rest.isEmpty) {
        return items.length == 1 ? items.first : null;
      }
      for (final w in items) {
        final name = w['name'] as String? ?? '';
        if (name.contains(rest) || rest.contains(name)) return w;
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  /// Phase 3 动作意图词：用户让 AI“帮我点/按/发/输入/滑”等
  static bool hasActionIntent(String text) {
    final t = text.trim();
    const patterns = [
      "帮我点", "帮我按", "帮我发", "帮我操作", "帮我输入", "帮我回复",
      "帮我打", "帮我滑动", "帮我滑", "帮我长按", "帮我点赞", "帮我播放",
      "帮我暂停", "点一下", "帮我发送", "帮我截图", "帮我退出", "帮我返回",
      "帮我回", "帮我回复", "帮我写",
      "回他", "回她", "回ta", "回复他", "回复她", "帮我切歌", "切歌",
    ];
    return patterns.any(t.contains);
  }

  /// 3.4a：把“帮我回/发/发布/点赞/播放”等意图解析为动作序列模板。
  /// 返回 {type, steps, content, sendText}；非模板意图返回 null。
  /// steps 元素：{action: click|long_click|set_text, target?, text?}
  /// [输入框] 为通用占位目标（Kotlin 节点树里空输入框以此标记，点击可聚焦）。
  static Map<String, dynamic>? parseActionTemplate(String text) {
    final t = text.trim();
    if (t.isEmpty) return null;
    String? content;
    String? type;
    // 引号字符集合：中文弯引号/直角引号/英文引号（\u 转义避免拆断字符串字面量）
    const quoteChars = "\u201C\u201D\u2018\u2019\u300E\u300F\u300C\u300D\"'";
    final quoted = RegExp("[$quoteChars]+([^$quoteChars]{1,50})[$quoteChars]+").firstMatch(t);
    if (quoted != null) {
      content = quoted.group(1)?.trim();
    }
    if (t.contains("发朋友圈") || t.contains("在朋友圈发") ||
        t.contains("发个朋友圈") || t.contains("发一条") || t.contains("发布")) {
      type = "publish";
    } else if (t.contains("帮我回") || t.contains("回复") || t.contains("帮我发") ||
        t.contains("帮我写") || t.contains("回他") || t.contains("回她") || t.contains("回ta")) {
      type = "reply";
    } else if (t.contains("点赞")) {
      type = "like";
    } else if (t.contains("播放") || t.contains("切歌")) {
      type = "play";
    }
    if (type == null) return null;
    content ??= t
        .replaceFirst(
            RegExp("^(帮我)?(回|回复|发|发布|发朋友圈|发个朋友圈|发一条朋友圈|在朋友圈发|写|点赞|播放|切歌)[$quoteChars]?"),
            "")
        .replaceAll(RegExp("[$quoteChars]"), "")
        .trim();
    // 防误判：无引号内容且剩余不足 2 字的陈述句（如“我发朋友圈了”）不当模板
    if ((type == "publish" || type == "reply") && quoted == null && content.length < 2) {
      return null;
    }
    if (type == "reply" && content.isEmpty) return null;

    final List<Map<String, dynamic>> steps;
    switch (type) {
      case "reply":
        steps = [
          {"action": "click", "target": "[输入框]"},
          {"action": "set_text", "text": content},
        ];
        break;
      case "publish":
        steps = [
          {"action": "click", "target": "发布动态"},
          {"action": "click", "target": "[输入框]"},
          {"action": "set_text", "text": content},
          {"action": "click", "target": "发布"},
        ];
        break;
      case "like":
        steps = [
          {"action": "click", "target": "点赞"},
        ];
        break;
      default:
        steps = [
          {"action": "click", "target": "播放"},
        ];
    }
    return {
      "type": type,
      "steps": steps,
      "content": content,
      // 回复模板：set_text 已写入本 app 输入框，由发送流程代为发送该内容
      "sendText": type == "reply" ? content : null,
    };
  }

  /// 3.4a：逐步执行序列。每步前等待界面稳定并重抓节点树（Kotlin 侧重新匹配），
  /// set_text 直接写入聚焦输入框；任一步失败立即停止。返回每步结果列表。
  /// 2026-08-14 双通道：click/long_click/scroll/set_text 走无障碍；
  /// launch_app/tap_xy/swipe/back/wait 走 Shizuku（ADB 级 input/am/monkey）。
  static Future<List<Map<String, dynamic>>> executeActionSequence(List<Map> steps) async {
    final results = <Map<String, dynamic>>[];
    for (var i = 0; i < steps.length; i++) {
      final r = await _executeSingleStep(steps[i], i + 1);
      results.add(r);
      if (!(r["ok"] as bool? ?? false)) break;
    }
    return results;
  }

  /// 图工作流执行（2026-08-14 方案 C）：nodes + edges，支持分支/条件/循环
  /// - 无 edges：按 nodes 顺序执行（等价旧 steps）
  /// - 有 edges：从第一个节点开始图遍历；每节点最多 3 次、总步数上限 30 防死循环
  static Future<List<Map<String, dynamic>>> executeWorkflowGraph(
    List<Map> nodes, {
    List<Map>? edges,
  }) async {
    final results = <Map<String, dynamic>>[];
    final edgeList = edges ?? const [];
    if (edgeList.isEmpty) {
      for (var i = 0; i < nodes.length; i++) {
        final r = await _executeSingleStep(nodes[i], i + 1);
        results.add(r);
        if (!(r["ok"] as bool? ?? false)) break;
      }
      return results;
    }
    final nodesById = <String, Map<String, dynamic>>{};
    for (final n in nodes) {
      final id = n["id"] as String? ?? "";
      if (id.isNotEmpty) nodesById[id] = Map<String, dynamic>.from(n);
    }
    String? currentId = nodes.isNotEmpty ? (nodes.first["id"] as String? ?? "") : "";
    final visitCount = <String, int>{};
    var total = 0;
    while (currentId != null && currentId.isNotEmpty) {
      if (total >= 30) {
        results.add({
          "step": total + 1, "action": "stop", "target": "",
          "ok": false, "message": "达到执行上限 30 步，已自动停止",
        });
        break;
      }
      final node = nodesById[currentId];
      if (node == null) break;
      visitCount[currentId] = (visitCount[currentId] ?? 0) + 1;
      if (visitCount[currentId]! > 3) {
        results.add({
          "step": total + 1, "action": "stop", "target": "",
          "ok": false, "message": "检测到重复循环，已自动停止",
        });
        break;
      }
      final r = await _executeSingleStep(node, total + 1);
      total++;
      results.add(r);
      final ok = r["ok"] as bool? ?? false;
      final outs = edgeList.where((e) => (e["from"] as String? ?? "") == currentId).toList();
      if (outs.isEmpty) break;
      Map? picked;
      final byResult = outs.where((e) {
        final t = e["type"] as String? ?? "success";
        return (t == "success" && ok) || (t == "fail" && !ok);
      }).toList();
      if (byResult.isNotEmpty) {
        picked = byResult.first;
      } else {
        final always = outs.where((e) => (e["type"] as String? ?? "") == "always").toList();
        if (always.isNotEmpty) {
          picked = always.first;
        } else {
          for (final e in outs) {
            final t = e["type"] as String? ?? "";
            if (t != "screen_has" && t != "screen_empty") continue;
            final has = await _screenHasText(e["target"] as String? ?? "");
            if ((t == "screen_has" && has) || (t == "screen_empty" && !has)) {
              picked = e;
              break;
            }
          }
        }
      }
      currentId = picked != null ? (picked["to"] as String? ?? "") : "";
    }
    return results;
  }

  /// 判断当前屏幕是否存在指定文本（screen_has / screen_empty 连线条件用）
  static Future<bool> _screenHasText(String text) async {
    final t = text.trim();
    if (t.isEmpty) return false;
    try {
      final r = await getNodeTree();
      final nodes = r["nodes"] as List? ?? const [];
      for (final n in nodes) {
        final label = (n["text"] as String? ?? "").trim();
        if (label.isNotEmpty && label.contains(t)) return true;
      }
    } catch (_) {}
    return false;
  }

  /// 单步执行（提取自 executeActionSequence，图/序列共用）
  static Future<Map<String, dynamic>> _executeSingleStep(Map step, int stepNo) async {
    await Future.delayed(const Duration(milliseconds: 700));
    final action = step["action"] as String? ?? "click";
    final Map<dynamic, dynamic> res;
    switch (action) {
      case "set_text":
        res = await setTextOnFocus(step["text"] as String? ?? "");
        break;
      case "launch_app":
      case "tap_xy":
      case "swipe":
      case "back":
      case "wait":
      case "go_home":
        res = await _runShizukuStep(step);
        break;
      default:
        res = await performAction(action, step["target"] as String? ?? "");
    }
    final ok = res["ok"] as bool? ?? false;
    final msg = res["message"] as String? ?? "执行完成";
    return {
      "step": stepNo,
      "action": action,
      "target": step["target"] ?? "",
      "ok": ok,
      "message": msg,
    };
  }

  /// Shizuku 通道步骤执行（2026-08-14）：input tap/swipe/text、am start/monkey、keyevent
  static Future<Map<dynamic, dynamic>> _runShizukuStep(Map step) async {
    final action = step["action"] as String? ?? "";
    if (action == "wait") {
      await Future.delayed(Duration(milliseconds: (step["ms"] as num? ?? 800).toInt()));
      return {"ok": true, "message": "等待完成"};
    }
    if (!Platform.isAndroid) return {"ok": false, "message": "非 Android"};
    try {
      final st = await ShizukuService.status();
      if (st["permissionGranted"] != true) {
        return {"ok": false, "message": "Shizuku 未授权，无法执行系统级操作"};
      }
      switch (action) {
        case "launch_app":
          final pkg = (step["target"] as String? ?? "").trim();
          if (pkg.isEmpty) return {"ok": false, "message": "缺少应用包名"};
          final r = await ShizukuService.runShell("monkey -p $pkg 1");
          final ok = r["ok"] == true;
          return {"ok": ok, "message": ok ? "已启动 $pkg" : "启动失败：${r['stderr'] ?? ''}"};
        case "tap_xy":
          final x = (step["x"] as num? ?? 0).toInt();
          final y = (step["y"] as num? ?? 0).toInt();
          final r = await ShizukuService.runShell("input tap $x $y");
          final ok = r["ok"] == true;
          return {"ok": ok, "message": ok ? "已点击 ($x, $y)" : "点击失败"};
        case "swipe":
          final x1 = (step["x1"] as num? ?? 0).toInt();
          final y1 = (step["y1"] as num? ?? 0).toInt();
          final x2 = (step["x2"] as num? ?? 0).toInt();
          final y2 = (step["y2"] as num? ?? 0).toInt();
          final dur = (step["ms"] as num? ?? 300).toInt();
          final r = await ShizukuService.runShell("input swipe $x1 $y1 $x2 $y2 $dur");
          final ok = r["ok"] == true;
          return {"ok": ok, "message": ok ? "已滑动" : "滑动失败"};
        case "back":
          final r = await ShizukuService.runShell("input keyevent 4");
          final ok = r["ok"] == true;
          return {"ok": ok, "message": ok ? "已返回" : "返回失败"};
        case "go_home":
          final r = await ShizukuService.runShell("input keyevent 3");
          final ok = r["ok"] == true;
          return {"ok": ok, "message": ok ? "已返回主页" : "返回主页失败"};
        default:
          return {"ok": false, "message": "未知 Shizuku 步骤：$action"};
      }
    } catch (e) {
      return {"ok": false, "message": "Shizuku 执行异常：$e"};
    }
  }
}
