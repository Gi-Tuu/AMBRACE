import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../global_keys.dart';
import '../providers/chat_provider.dart';
import '../screens/chat/chat_screen.dart';
import '../widgets/app_page_route.dart';
import 'api_client.dart';

/// FCM 离线推送服务（2026-08-28）。
///
/// 编译开关：flutter build --dart-define=ENABLE_FCM=true
/// 默认 APK 包含 firebase 依赖但不初始化（enableFcm=false 时 init() 直接 return），
/// 不影响没有 Google Play 服务的设备。
///
/// 初始化流程：
/// 1. 检查 ENABLE_FCM 编译开关
/// 2. 从 SharedPreferences 读取 server_url/auth_token 并自行配置 ApiClient（init 可能先于登录后 configure 执行）
/// 3. 从后端 GET /api/v1/device/fcm-config 获取 Firebase 客户端配置
/// 4. 用 FirebaseOptions 手动初始化（不依赖 google-services.json）
/// 5. 请求权限 → 获取 token → 注册到后端
/// 6. 监听前台消息/后台点击/token 刷新
class FcmPushService {
  static final FcmPushService instance = FcmPushService._();
  FcmPushService._();

  /// 编译时开关：ENABLE_FCM=true 才启用
  static const bool enableFcm = bool.fromEnvironment('ENABLE_FCM', defaultValue: false);

  static const _prefsDeviceId = 'fcm_device_id';
  static const _heartbeatInterval = Duration(minutes: 5);
  static const _chatChannelId = 'ai_companion_chat';
  static const _alertChannelId = 'ai_companion_alert';

  bool _initialized = false;
  Timer? _heartbeatTimer;
  String? _deviceId;
  FlutterLocalNotificationsPlugin? _fln;

  bool get isAvailable => _initialized;

  Future<void> init() async {
    // 已初始化：只重新取 token 注册（token 可能已刷新或登录账号变化），不重复初始化 Firebase。
    // 这在登录/注册/引导页 configure 成功后再调用时安全（可重入）。
    if (_initialized) {
      try {
        final fm = FirebaseMessaging.instance;
        await _registerToken(fm);
      } catch (e) {
        debugPrint('[FCM] re-register token failed: $e');
      }
      return;
    }

    if (!enableFcm) {
      debugPrint('[FCM] disabled at compile time (ENABLE_FCM not set)');
      return;
    }

    // 从 SharedPreferences 读取 server_url/auth_token 并自行配置 ApiClient。
    // main() 里的 init() 在 MultiProvider/ApiClient.configure 之前执行，因此这里必须自给自足；
    // 未配置服务器地址时直接返回，等登录页 configure 成功后再次调用 init()。
    final prefs = await SharedPreferences.getInstance();
    final serverUrl = prefs.getString('server_url') ?? '';
    if (serverUrl.isEmpty) {
      debugPrint('[FCM] server_url not set, skip init');
      return;
    }
    final authToken = prefs.getString('auth_token') ?? '';
    if (authToken.isEmpty) {
      debugPrint('[FCM] not logged in, skip init');
      return;
    }
    ApiClient().configure(baseUrl: serverUrl, token: authToken);

    // 从后端获取 Firebase 客户端配置
    Map<String, dynamic>? fcmConfig;
    try {
      final resp = await ApiClient().dio.get('/api/v1/device/fcm-config');
      final data = resp.data as Map<String, dynamic>;
      if (data['enabled'] == true) {
        fcmConfig = data;
      }
    } catch (e) {
      debugPrint('[FCM] fetch fcm-config failed: $e');
      return;
    }

    if (fcmConfig == null) {
      debugPrint('[FCM] FCM not enabled on server');
      return;
    }

    // 手动初始化 Firebase（不需要 google-services.json）
    try {
      await Firebase.initializeApp(
        options: FirebaseOptions(
          apiKey: fcmConfig['apiKey'] as String? ?? '',
          appId: fcmConfig['appId'] as String? ?? '',
          messagingSenderId: fcmConfig['messagingSenderId'] as String? ?? '',
          projectId: fcmConfig['projectId'] as String? ?? '',
          storageBucket: fcmConfig['storageBucket'] as String?,
        ),
      );
      // 必须在 Firebase 初始化后注册后台消息处理器，否则顶层函数是死代码。
      FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);
    } catch (e) {
      debugPrint('[FCM] Firebase.initializeApp failed (no GMS?): $e');
      return;
    }

    final fm = FirebaseMessaging.instance;

    // 请求通知权限
    try {
      final settings = await fm.requestPermission(
        alert: true,
        badge: true,
        sound: true,
      );
      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        debugPrint('[FCM] notification permission denied');
        return;
      }
    } catch (e) {
      debugPrint('[FCM] requestPermission failed: $e');
      return;
    }

    _initialized = true;
    _deviceId = await _getDeviceId();

    await _registerToken(fm);
    fm.onTokenRefresh.listen(_registerTokenWithBackend);
    _setupForegroundHandler();
    _setupTapHandlers(fm);
    _startHeartbeat();

    debugPrint('[FCM] initialized, deviceId=$_deviceId');
  }

  Future<void> _registerToken(FirebaseMessaging fm) async {
    try {
      final token = await fm.getToken();
      if (token != null) {
        await _registerTokenWithBackend(token);
      }
    } catch (e) {
      debugPrint('[FCM] getToken failed: $e');
    }
  }

  Future<void> _registerTokenWithBackend(String token) async {
    if (_deviceId == null) return;
    try {
      await ApiClient().dio.post('/api/v1/device/register', data: {
        'device_id': _deviceId,
        'platform': 'android',
        'push_provider': 'fcm',
        'push_token': token,
        'app_version': const String.fromEnvironment('APP_VERSION', defaultValue: ''),
      });
    } catch (e) {
      debugPrint('[FCM] register token failed: $e');
    }
  }

  void _setupForegroundHandler() {
    _fln = FlutterLocalNotificationsPlugin();
    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    const initSettings = InitializationSettings(android: androidInit);

    // Android 8+ 必须先显式创建通知渠道，否则通知不会展示。
    try {
      final androidImpl = _fln!
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();
      androidImpl?.createNotificationChannel(const AndroidNotificationChannel(
        _chatChannelId,
        'AI 消息',
        description: 'AI 好友的新消息',
        importance: Importance.high,
      ));
      androidImpl?.createNotificationChannel(const AndroidNotificationChannel(
        _alertChannelId,
        '重要提醒',
        description: '查岗等重要通知',
        importance: Importance.high,
      ));
    } catch (e) {
      debugPrint('[FCM] createNotificationChannel failed: $e');
    }

    _fln!.initialize(
      settings: initSettings,
      onDidReceiveNotificationResponse: (resp) {
      final payload = resp.payload;
      if (payload != null && payload.isNotEmpty) {
        try {
          final data = jsonDecode(payload) as Map<String, dynamic>;
          handleDeepLink(data);
        } catch (_) {}
      }
    });

    FirebaseMessaging.onMessage.listen((msg) {
      final notif = msg.notification;
      final data = msg.data;
      if (notif != null) {
        _showLocalNotification(notif.title ?? '', notif.body ?? '', data);
      }
    });
  }

  Future<void> _showLocalNotification(
    String title,
    String body,
    Map<String, dynamic> data,
  ) async {
    final isAlert = data['channel'] == 'alert';
    final androidDetails = AndroidNotificationDetails(
      isAlert ? _alertChannelId : _chatChannelId,
      isAlert ? '重要提醒' : 'AI 消息',
      channelDescription: isAlert ? '查岗等重要通知' : 'AI 好友的新消息',
      importance: Importance.high,
      priority: Priority.high,
    );
    await _fln?.show(
      id: DateTime.now().millisecondsSinceEpoch.remainder(1 << 31),
      title: title,
      body: body,
      notificationDetails: NotificationDetails(android: androidDetails),
      payload: jsonEncode(data),
    );
  }

  void _setupTapHandlers(FirebaseMessaging fm) {
    fm.getInitialMessage().then((msg) {
      if (msg != null) handleDeepLink(msg.data);
    });
    FirebaseMessaging.onMessageOpenedApp.listen((msg) {
      handleDeepLink(msg.data);
    });
  }

  void _startHeartbeat() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = Timer.periodic(_heartbeatInterval, (_) => _heartbeat());
  }

  Future<void> _heartbeat() async {
    if (!_initialized || _deviceId == null) return;
    try {
      await ApiClient().dio.post('/api/v1/device/heartbeat', data: {
        'device_id': _deviceId,
        'push_provider': 'fcm',
      });
    } catch (_) {}
  }

  Future<String> _getDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_prefsDeviceId);
    if (id == null || id.isEmpty) {
      final rand = Random.secure();
      final bytes = List<int>.generate(16, (_) => rand.nextInt(256));
      id = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
      await prefs.setString(_prefsDeviceId, id);
    }
    return id;
  }

  /// 退出登录时注销 token
  Future<void> unregister() async {
    if (!_initialized || _deviceId == null) return;
    try {
      await ApiClient().dio.delete(
        '/api/v1/device/unregister',
        queryParameters: {'device_id': _deviceId, 'push_provider': 'fcm'},
      );
    } catch (_) {}
    _heartbeatTimer?.cancel();
  }

  /// 处理通知点击深链
  static int _deepLinkRetries = 0;

  static void handleDeepLink(Map<String, dynamic> data) {
    final route = data['route'] as String?;
    if (route != 'chat') return;

    final characterId = int.tryParse(data['character_id']?.toString() ?? '');
    if (characterId == null) return;

    final ctx = appNavigatorKey.currentContext;
    if (ctx == null) {
      // 导航树未就绪（如冷启动点通知）：最多重试 10 次（约 20 秒），避免异常状态无限重试
      if (_deepLinkRetries < 10) {
        _deepLinkRetries++;
        Timer(const Duration(seconds: 2), () => handleDeepLink(data));
      }
      return;
    }
    _deepLinkRetries = 0;

    _openChat(characterId);
  }

  static Future<void> _openChat(int characterId) async {
    final ctx = appNavigatorKey.currentContext;
    if (ctx == null) return;

    try {
      final chars = await ApiClient().getCharacters();
      final char = chars.where((c) => c.id == characterId).firstOrNull;
      if (char == null) return;

      if (!ctx.mounted) return;
      ctx.read<ChatProvider>().setCharacter(char);
      await Future.delayed(const Duration(milliseconds: 200));
      if (ctx.mounted) {
        Navigator.push(
          ctx,
          AppPageRoute(builder: (_) => const ChatScreen()),
        );
      }
    } catch (_) {}
  }
}

/// 后台 FCM 消息处理器（顶层函数，firebase_messaging 要求）。
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  debugPrint('[FCM] background message: ${message.messageId}');
}
