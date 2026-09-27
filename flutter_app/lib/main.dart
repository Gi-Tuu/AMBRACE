
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import 'providers/chat_provider.dart';
import 'services/notification_service.dart';
import 'services/api_client.dart';
import 'services/background_polling_service.dart';
import 'services/fcm_push_service.dart';
import 'global_keys.dart';
import 'providers/settings_provider.dart';
import 'theme/app_theme.dart';
import 'theme/skins/skin_registry.dart';
import 'widgets/app_background.dart';
import 'providers/moments_provider.dart';
import 'providers/characters_provider.dart';
import 'providers/diary_provider.dart';
import 'providers/pets_provider.dart';
import 'features/home/home_screen.dart';
import 'features/auth/login_screen.dart';
import 'features/auth/pair_server_screen.dart';
import 'services/server_identity.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 显式初始化皮肤注册表（注册内置皮肤；未来插件可在此后注册自定义皮肤）
  SkinRegistry.initialize();
  _setupLifecycleObserver();
  // 2026-09-13 黑屏修复：runApp 之前不再 await 任何服务初始化。
  // 原因：FcmPushService.init() 会做网络请求（device/fcm-config、register）+ 系统通知权限弹窗 + 取 token；
  // 网络慢或权限弹窗没人应答时，首帧被一直挡住，用户看到的就是「打开后长时间黑屏」。
  // 现在先渲染首帧（登录页/引导页立即可见），三个服务在首帧后异步初始化，任一失败都只降级不影响使用。
  runApp(const AICompanionApp());
  WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrapServices());
}

/// 首帧之后的后台初始化（顺序执行；每个都 try/catch 降级，任何异常不影响 App 使用）。
Future<void> _bootstrapServices() async {
  try {
    await NotificationService().init();
  } catch (e) {
    debugPrint('Notification init failed: $e');
  }
  // 注册前台服务配置（真正启动在登录后 home_screen）
  try {
    await BackgroundPollingService.ensureConfigured();
  } catch (e) {
    debugPrint('Background service configure failed: $e');
  }
  // FCM 离线推送（ENABLE_FCM=true 时才初始化，否则直接 return）—— init 内部自给自足读取 server_url/token
  try {
    await FcmPushService.instance.init();
  } catch (e) {
    debugPrint('FCM init failed: $e');
  }
}

/// 监听 app 前后台：写 app_in_foreground 标志，供前台服务与 Flutter 层双源去重
void _setupLifecycleObserver() {
  AppLifecycleListener(
    onShow: () => NotificationService().setAppInForeground(true),
    onHide: () => NotificationService().setAppInForeground(false),
  );
}

// ---- 批 0-3 M0-b 收尾：服务器身份配对入口（方案 §4.3.4）----

/// 栈顶路由名：配对页自身不再显示入口，避免与页面内容重复
final ValueNotifier<String> _topRouteName = ValueNotifier<String>('');
final NavigatorObserver _topRouteNameObserver = _TopRouteNameObserver();

class _TopRouteNameObserver extends NavigatorObserver {
  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previous) {
    _topRouteName.value = route.settings.name ?? '';
  }

  @override
  void didPop(Route<dynamic> route, Route<dynamic>? previous) {
    _topRouteName.value = previous?.settings.name ?? '';
  }
}

/// 「尚未与该服务器配对」提示 + 配对入口。
/// 只在身份读取完成且本机未保存当前服务器身份时出现；已配对（含后端 off 未出签）完全不打扰。
class _ServerIdentityPairEntry extends StatefulWidget {
  const _ServerIdentityPairEntry();

  @override
  State<_ServerIdentityPairEntry> createState() => _ServerIdentityPairEntryState();
}

class _ServerIdentityPairEntryState extends State<_ServerIdentityPairEntry> {
  bool _dismissed = false;

  @override
  void initState() {
    super.initState();
    // 只后台读取，读不到（prefs 不可用/无网络）就当未配对处理，绝不影响既有渲染
    ServerIdentity.instance.ensureLoaded().catchError((Object _) {}).then((_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> _openPairing() async {
    await Navigator.of(context).pushNamed('/pair-server');
    if (mounted) setState(() {}); // 配对/解除配对返回后立刻刷新提示
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final identity = ServerIdentity.instance;
    return ValueListenableBuilder<String>(
      valueListenable: _topRouteName,
      builder: (context, topRoute, _) {
        if (_dismissed ||
            !identity.isLoaded ||
            identity.isPaired ||
            topRoute == '/pair-server') {
          return const SizedBox.shrink();
        }
        // 换过服务器地址（本机存有旧指纹）时提示语不同于首次未配对
        final hint = identity.fingerprint.isNotEmpty
            ? l10n.serverIdentityReconnectNeeded
            : l10n.serverIdentityEntryHint;
        return Align(
          alignment: Alignment.bottomLeft,
          child: Padding(
            padding: EdgeInsets.only(
                left: 12,
                // 抬到各页底部导航条之上，不遮挡既有操作
                bottom: MediaQuery.paddingOf(context).bottom + 88),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.only(left: 12, top: 2, bottom: 2),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: Text(hint,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodySmall),
                    ),
                    TextButton(
                      onPressed: _openPairing,
                      child: Text(l10n.serverIdentityPair),
                    ),
                    IconButton(
                      icon: const Icon(Icons.close, size: 16),
                      onPressed: () => setState(() => _dismissed = true),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 解析语言：system=跟随设备语言（非 zh/en 一律回退简体中文），zh/en 直接使用。
Locale _resolveLocale(String code) {
  if (code == 'zh') return const Locale('zh');
  if (code == 'en') return const Locale('en');
  final sys = WidgetsBinding.instance.platformDispatcher.locale;
  if (sys.languageCode == 'zh' || sys.languageCode == 'en') return Locale(sys.languageCode);
  return const Locale('zh');
}

class AICompanionApp extends StatefulWidget {
  const AICompanionApp({super.key});

  @override
  State<AICompanionApp> createState() => _AICompanionAppState();
}

class _AICompanionAppState extends State<AICompanionApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// 跟随系统时：设备语言切换后立即重建 MaterialApp，界面语言随之更新。
  @override
  void didChangeLocales(List<Locale>? locales) {
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => SettingsProvider()),
        ChangeNotifierProvider(create: (_) => MomentsProvider()),
        ChangeNotifierProvider(create: (_) => CharactersProvider()),
        ChangeNotifierProvider(create: (_) => DiaryProvider()),
        ChangeNotifierProvider(create: (_) => PetsProvider()),
        ChangeNotifierProxyProvider<SettingsProvider, ChatProvider>(
          create: (ctx) {
            final settings = ctx.read<SettingsProvider>();
            ApiClient().configure(baseUrl: settings.serverUrl, token: settings.token);
            return ChatProvider();
          },
          update: (ctx, settings, previous) {
            ApiClient().configure(baseUrl: settings.serverUrl, token: settings.token);
            final cp = previous ?? ChatProvider();
            cp.setLocaleCode(settings.localeCode);
            return cp;
          },
        ),
      ],
      child: Consumer<SettingsProvider>(
        builder: (context, settings, _) {
          // F7-c：401 统一处理——清登录态并回登录页（触发条件与 3s 去重在 ApiClient 拦截器）
          ApiClient().onUnauthorized = () {
            if (settings.token.isEmpty) return;
            settings.logout();
            appNavigatorKey.currentState?.pushNamedAndRemoveUntil('/login', (r) => false);
          };
          return MaterialApp(
            title: 'AMBRACE',
            debugShowCheckedModeBanner: false,
            navigatorKey: appNavigatorKey,
            navigatorObservers: [appRouteObserver, _topRouteNameObserver],
            theme: AppTheme.light(settings.seedColorIndex, skinId: settings.skinId, fontVariant: settings.fontVariant),
            darkTheme: AppTheme.dark(settings.seedColorIndex, skinId: settings.skinId, fontVariant: settings.fontVariant),
            themeMode: AppTheme.modeFromIndex(settings.themeModeIndex),
            builder: (context, child) => Stack(
              children: [
                const AppBackground(), // 全局背景层（最底层，登录页也生效）
                child ?? const SizedBox(),
                const _ServerIdentityPairEntry(), // 未配对提示（已配对时不渲染任何像素）
              ],
            ),
            locale: _resolveLocale(settings.localeCode),
            localizationsDelegates: const [
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
              ...AppLocalizations.localizationsDelegates,
            ],
            supportedLocales: AppLocalizations.supportedLocales,
            home: const LoginScreen(),
            routes: {
              '/home': (context) => const HomeScreen(),
              '/login': (context) => const LoginScreen(),
              '/pair-server': (context) => const PairServerScreen(),
            },
          );
        },
      ),
    );
  }
}
