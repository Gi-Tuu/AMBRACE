
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
import 'screens/home/home_screen.dart';
import 'screens/auth/login_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 显式初始化皮肤注册表（注册内置皮肤；未来插件可在此后注册自定义皮肤）
  SkinRegistry.initialize();
  // 初始化失败不允许阻塞启动（闪退防御：任何初始化异常都降级，不影响打开 app）
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
  _setupLifecycleObserver();
  runApp(const AICompanionApp());
}

/// 监听 app 前后台：写 app_in_foreground 标志，供前台服务与 Flutter 层双源去重
void _setupLifecycleObserver() {
  AppLifecycleListener(
    onShow: () => NotificationService().setAppInForeground(true),
    onHide: () => NotificationService().setAppInForeground(false),
  );
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
          return MaterialApp(
            title: 'AMBRACE',
            debugShowCheckedModeBanner: false,
            navigatorKey: appNavigatorKey,
            navigatorObservers: [appRouteObserver],
            theme: AppTheme.light(settings.seedColorIndex, skinId: settings.skinId),
            darkTheme: AppTheme.dark(settings.seedColorIndex, skinId: settings.skinId),
            themeMode: AppTheme.modeFromIndex(settings.themeModeIndex),
            builder: (context, child) => Stack(
              children: [
                const AppBackground(), // 全局背景层（最底层，登录页也生效）
                child ?? const SizedBox(),
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
            },
          );
        },
      ),
    );
  }
}
