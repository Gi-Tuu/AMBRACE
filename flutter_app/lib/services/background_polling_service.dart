import "dart:async";
import "dart:ui";
import "package:flutter/foundation.dart";
import "package:dio/dio.dart";
import "package:flutter_background_service/flutter_background_service.dart";
import "package:flutter_local_notifications/flutter_local_notifications.dart";
import "package:shared_preferences/shared_preferences.dart";
import "phone_perception_service.dart";
import "unread_engine.dart";

/// 前台服务（Android Foreground Service）：app 退后台/息屏后继续轮询未读并弹系统通知。
/// 【通知双轨收敛 2026-08-05】本服务是唯一的网络轮询源：
/// 每次轮询结果写入 SharedPreferences（快照/新增事件/心跳），
/// 前台 Flutter 层（NotificationService）只读快照负责红点与横幅，不重复轮询；
/// app 在前台时本服务仅写快照与事件不弹系统通知（横幅由前台负责）。
class BackgroundPollingService {
  static const int _serviceNotificationId = 8888;
  static const String _serviceChannelId = "ai_companion_service";

  /// 必须在主 isolate 调用一次（main 中）
  static Future<void> ensureConfigured() async {
    // 必须先创建前台服务用的 NotificationChannel：
    // flutter_background_service 插件在配置了自定义 notificationChannelId 时不会自动创建 channel（插件 bug），
    // channel 不存在会令 startForeground 抛 CannotPostForegroundServiceNotificationException（Android 14+ 直接闪退）
    try {
      final plugin = FlutterLocalNotificationsPlugin();
      await plugin.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings("@mipmap/ic_launcher"),
          iOS: DarwinInitializationSettings(),
        ),
      );
      await plugin
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(
            const AndroidNotificationChannel(
              _serviceChannelId,
              "拥爱后台服务",
              description: "后台轮询 AI 好友新消息的常驻通知",
              importance: Importance.low,
              playSound: false,
            ),
          );
    } catch (_) {}
    await FlutterBackgroundService().configure(
      androidConfiguration: AndroidConfiguration(
        onStart: onStart,
        autoStart: false,
        autoStartOnBoot: true,
        isForegroundMode: true,
        foregroundServiceTypes: [AndroidForegroundType.dataSync],
        initialNotificationTitle: "拥爱运行中",
        initialNotificationContent: "正在后台监听 AI 好友的新消息",
        notificationChannelId: _serviceChannelId,
        foregroundServiceNotificationId: _serviceNotificationId,
      ),
      iosConfiguration: IosConfiguration(
        autoStart: false,
        onForeground: onStart,
      ),
    );
  }

  /// 登录成功后启动（app 处于前台，满足 Android 14+ 前台服务启动限制）
  static Future<void> start() async {
    await ensureConfigured();
    await FlutterBackgroundService().startService();
  }

  /// 登出/应用退出时停止
  static Future<void> stop() async {
    FlutterBackgroundService().invoke("stopService");
  }
}

/// 后台 isolate 入口（必须是顶层函数）
@pragma("vm:entry-point")
void onStart(ServiceInstance service) async {
  DartPluginRegistrant.ensureInitialized();

  final plugin = FlutterLocalNotificationsPlugin();
  await plugin.initialize(
    const InitializationSettings(
      android: AndroidInitializationSettings("@mipmap/ic_launcher"),
      iOS: DarwinInitializationSettings(),
    ),
  );

  // 可靠通道：主 isolate 前后台切换时 invoke 通知本 isolate
  // （SharedPreferences 跨 isolate 单例缓存读不到对方写入值，不能作为标志传递）
  service.on("setAppForeground").listen((data) {
    _appInForeground = data?["value"] as bool? ?? false;
  });

  Timer? timer;
  if (service is AndroidServiceInstance) {
    service.on("stopService").listen((_) {
      timer?.cancel();
      service.stopSelf();
    });
  }

  // 先轮询一次建立基线（不弹），之后每 15 秒增量比对（2026-08-18：30s→15s，小红点产生更灵敏）
  timer = Timer.periodic(const Duration(seconds: 15), (_) => _pollOnce(plugin));
  await _pollOnce(plugin);
}

// ---- 后台 isolate 内部状态 ----
/// app 是否在前台（主 isolate 通过 invoke("setAppForeground") 维护；登录启动时在前台）
bool _appInForeground = true;
Map<int, int> _lastCounts = {};
bool _baselineSet = false;
final Map<int, int> _sessionMap = {};
final Map<int, String> _charNames = {};
final NotifyDebouncer _debouncer = NotifyDebouncer();

/// 启动首轮立即上报一次（建立基线/恢复遗漏），之后每 10 次轮询（≈5 分钟）上报：
/// AI 主动提通知（手机感知开关控制，服务器端另有 30 分钟节流）
int _pollCount = 0;

Future<void> _autoReportNotifications() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    if (!(prefs.getBool(PhonePerceptionService.autoNotifyKey) ?? false)) return;
    final baseUrl = prefs.getString("server_url") ?? "";
    final token = prefs.getString("auth_token") ?? "";
    if (baseUrl.isEmpty || token.isEmpty) return;
    final notifs = await PhonePerceptionService.readCachedNotifications();
    if (notifs.isEmpty) return;
    final dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      headers: {"Authorization": "Bearer $token"},
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 10),
    ));
    await dio.post("/api/v1/phone/perception/auto", data: {"notifications": notifs});
  } catch (_) {}
}

Future<void> _pollOnce(FlutterLocalNotificationsPlugin plugin) async {
  try {
    final prefs = await SharedPreferences.getInstance();
    final baseUrl = prefs.getString("server_url") ?? "";
    final token = prefs.getString("auth_token") ?? "";
    if (baseUrl.isEmpty || token.isEmpty) return;
    // 用可靠通道维护的内存标志（prefs 跨 isolate 读不到，不能依赖）
    final inForeground = _appInForeground;
    final notificationsEnabled = prefs.getBool("notifications_enabled") ?? true;
    final dnd = {
      "enabled": prefs.getBool("dnd_enabled") ?? false,
      "startHour": prefs.getInt("dnd_start_hour") ?? 22,
      "startMinute": prefs.getInt("dnd_start_minute") ?? 0,
      "endHour": prefs.getInt("dnd_end_hour") ?? 8,
      "endMinute": prefs.getInt("dnd_end_minute") ?? 0,
    };

    final dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      headers: {"Authorization": "Bearer $token"},
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 10),
    ));

    final resp = await dio.get("/api/v1/chat/unread");
    final list = resp.data["unread"] as List? ?? [];
    _sessionMap.clear();
    for (final item in list) {
      if (item is! Map) continue;
      final sid = item["session_id"] as int?;
      final cid = item["character_id"] as int?;
      if (sid != null && cid != null) _sessionMap[cid] = sid;
    }
    final newCounts = UnreadEngine.parseUnread(list);

    // 增量比对 → 新增未读（首轮只建基线，不产生事件）
    final diffs = _baselineSet
        ? UnreadEngine.diffNewUnread(_lastCounts, newCounts)
        : <UnreadDiff>[];

    // 组装完整事件（拉最近 AI 消息内容 + 角色名）→ 写 prefs 供前台横幅/红点
    final events = <UnreadEvent>[];
    for (final diff in diffs) {
      final sid = _sessionMap[diff.characterId];
      if (sid == null) continue;
      final content = await _fetchLastAiContent(dio, sid);
      if (content == null || content.isEmpty) continue;
      final title = await _characterName(dio, diff.characterId);
      events.add(UnreadEvent(
        characterId: diff.characterId,
        sessionId: sid,
        count: diff.count,
        title: title,
        content: content,
      ));
    }

    // app 后台时才由本服务弹系统通知；前台由 Flutter 层横幅负责。
    // 弹通知放在 prefs 写入之前：即使 prefs 写入失败也不影响通知送达。
    if (!inForeground && notificationsEnabled && !UnreadEngine.isInDndPeriod(dnd)) {
      for (final event in events) {
        if (!_debouncer.shouldAllow(event.characterId)) continue;
        await _showSystemNotification(plugin, event);
      }
    }

    // prefs 写入（事件/快照/心跳）独立 try/catch：跨 isolate 并发写偶发异常，
    // 失败不阻塞弹通知、不影响基线更新，避免事件重复写入
    try {
      final nowMs = DateTime.now().millisecondsSinceEpoch;
      if (events.isNotEmpty) {
        await prefs.setString(NotifyPrefs.newEventKey, NotifyPrefs.encodeEvent(events));
        await prefs.setInt(NotifyPrefs.newEventAtKey, nowMs);
      }
      await prefs.setString(NotifyPrefs.snapshotKey, NotifyPrefs.encodeSnapshot(newCounts));
      await prefs.setInt(NotifyPrefs.snapshotAtKey, nowMs);
      await prefs.setInt(NotifyPrefs.lastPollAtKey, nowMs);
    } catch (e) {
      debugPrint("pollOnce prefs write error: $e");
    }

    // 查岗请求（2026-08-15）：角色想感知用户手机时，采集系统快照（前台应用）上报
    try {
      final ciResp = await dio.get("/api/v1/phone/perception/check-in-request");
      final ciData = ciResp.data as Map? ?? {};
      if (ciData["has"] == true) {
        final reqId = ciData["id"] as int? ?? 0;
        // 手机感知总开关开启才采集（避免无授权时反复尝试）
        final perceptionOn = prefs.getBool(PhonePerceptionService.enabledKey) ?? false;
        if (perceptionOn) {
          await PhonePerceptionService.uploadShizukuSnapshotIfAvailable();
        }
        if (reqId > 0) {
          try {
            await dio.post("/api/v1/phone/perception/check-in-request/$reqId/done");
          } catch (_) {}
        }
      }
    } catch (_) {}

    // 家庭群聊 @ 我的才弹（2026-08-15）：轮询被 @ 角色的回应，增量弹系统通知（仅后台）
    try {
      final groupCursor = prefs.getInt("group_mention_cursor") ?? 0;
      final mResp = await dio.get(
        "/api/v1/chat-groups/mentions",
        queryParameters: {"after_id": groupCursor},
      );
      final mItems = (mResp.data["items"] as List? ?? []).cast<Map>();
      if (mItems.isNotEmpty) {
        var maxId = groupCursor;
        for (final m in mItems) {
          final mid = (m["id"] as num?)?.toInt() ?? 0;
          if (mid > maxId) maxId = mid;
        }
        if (!_baselineSet) {
          // 首轮只建基线，不弹
          await prefs.setInt("group_mention_cursor", maxId);
        } else {
          final fresh = mItems
              .where((m) => ((m["id"] as num?)?.toInt() ?? 0) > groupCursor)
              .toList();
          if (!inForeground && notificationsEnabled && !UnreadEngine.isInDndPeriod(dnd)) {
            for (final m in fresh) {
              final title =
                  '[${m["group_name"]}] ${m["sender_name"] ?? ""}';
              final content = (m["content"] as String? ?? "").toString();
              if (content.isEmpty) continue;
              const androidDetails = AndroidNotificationDetails(
                "ai_companion_chat",
                "聊天消息",
                channelDescription: "AI好友的新消息通知",
                importance: Importance.high,
                priority: Priority.high,
              );
              await plugin.show(
                90000 + ((m["id"] as num?)?.toInt() ?? 0),
                title,
                content,
                const NotificationDetails(
                  android: androidDetails,
                  iOS: DarwinNotificationDetails(),
                ),
              );
            }
          }
          await prefs.setInt("group_mention_cursor", maxId);
        }
      }
    } catch (_) {}

    _lastCounts = newCounts;
    _baselineSet = true;
  } catch (e) {
    debugPrint("pollOnce error: $e");
  }
  // AI 主动提通知：启动首轮立即上报（快速建基线/恢复遗漏），之后每 5 分钟一次
  //（服务器端负责指纹对比与 30 分钟节流，频繁上报无额外打扰）
  _pollCount++;
  if (_pollCount == 1 || _pollCount % 10 == 0) {
    await _autoReportNotifications();
  }
}

/// 拉取会话最近一条 AI 消息内容（弹窗/事件共用）
Future<String?> _fetchLastAiContent(Dio dio, int sessionId) async {
  try {
    final msgsResp = await dio.get(
      "/api/v1/chat/sessions/$sessionId/messages",
      queryParameters: {"limit": 5},
    );
    final msgs = msgsResp.data["messages"] as List? ?? [];
    for (final m in msgs.reversed) {
      if (m is Map && m["sender_type"] == "ai") {
        final content = m["content"] as String?;
        if (content != null && content.isNotEmpty) return content;
      }
    }
  } catch (_) {}
  return null;
}

Future<void> _showSystemNotification(
    FlutterLocalNotificationsPlugin plugin, UnreadEvent event) async {
  try {
    const androidDetails = AndroidNotificationDetails(
      "ai_companion_chat",
      "聊天消息",
      channelDescription: "AI好友的新消息通知",
      importance: Importance.high,
      priority: Priority.high,
    );
    await plugin.show(
      event.characterId,
      event.title,
      event.content,
      const NotificationDetails(
        android: androidDetails,
        iOS: DarwinNotificationDetails(),
      ),
    );
  } catch (_) {}
}

Future<String> _characterName(Dio dio, int charId) async {
  final cached = _charNames[charId];
  if (cached != null) return cached;
  try {
    final resp = await dio.get("/api/v1/characters");
    final list = resp.data["characters"] as List? ?? [];
    for (final c in list) {
      if (c is Map) {
        final id = c["id"] as int?;
        final name = c["name"] as String?;
        if (id != null && name != null) _charNames[id] = name;
      }
    }
    return _charNames[charId] ?? "AI 好友";
  } catch (_) {
    return "AI 好友";
  }
}