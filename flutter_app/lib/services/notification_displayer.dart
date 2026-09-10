import "package:flutter_local_notifications/flutter_local_notifications.dart";
import "dnd_settings.dart";
import "../utils/app_lang.dart";
import "../utils/service_l10n.dart";

/// 系统通知展示器：初始化本地通知插件并弹出系统通知。
/// 弹窗决策（是否弹、防抖、页面抑制）由 NotificationService 负责，
/// 本类只负责"执行弹窗"。
class NotificationDisplayer {
  static final NotificationDisplayer _instance =
      NotificationDisplayer._internal();
  factory NotificationDisplayer() => _instance;
  NotificationDisplayer._internal();

  FlutterLocalNotificationsPlugin? _notifications;

  /// 初始化通知插件并申请权限（Android 13+ 横幅/系统通知需要）
  Future<void> init() async {
    final l10n = ServiceL10n(await appLang());
    final channelName = l10n.notifChannelMessages;
    final channelDesc = l10n.notifChannelMessagesDesc;
    _notifications = FlutterLocalNotificationsPlugin();
    const androidSettings = AndroidInitializationSettings("@mipmap/ic_launcher");
    const iosSettings = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    const initSettings = InitializationSettings(
      android: androidSettings,
      iOS: iosSettings,
    );
    await _notifications!.initialize(settings: initSettings);
    try {
      final android = _notifications!.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      // 显式创建聊天消息 channel（Android 8+ 要求；后台 isolate 同源补建，双保险）
      await android?.createNotificationChannel(
        AndroidNotificationChannel(
          "ai_companion_chat",
          channelName,
          description: channelDesc,
          importance: Importance.high,
          playSound: true,
        ),
      );
      await android?.requestNotificationsPermission();
    } catch (_) {}
  }

  /// 弹出系统通知（含通知总开关与免打扰时段检查）
  Future<void> showSystemNotification({
    required int id,
    required String title,
    required String body,
  }) async {
    if (_notifications == null) return;
    final l10n = ServiceL10n(await appLang());
    final channelName = l10n.notifChannelMessages;
    final channelDesc = l10n.notifChannelMessagesDesc;
    final settings = await DndSettings.get();
    if (!(settings["notificationsEnabled"] as bool? ?? true)) return;
    if (DndSettings.isInDndPeriod(settings)) return;

    final androidDetails = AndroidNotificationDetails(
      "ai_companion_chat",
      channelName,
      channelDescription: channelDesc,
      importance: Importance.high,
      priority: Priority.high,
    );
    final details = NotificationDetails(
      android: androidDetails,
      iOS: DarwinNotificationDetails(),
    );
    await _notifications!.show(id: id, title: title, body: body, notificationDetails: details);
  }
}
