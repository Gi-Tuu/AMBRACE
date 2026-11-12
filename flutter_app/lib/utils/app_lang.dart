import 'dart:ui';

import 'package:shared_preferences/shared_preferences.dart';

/// 读取当前界面语言（无 BuildContext 的服务类共用）：返回 zh / en
/// 与 SettingsProvider.localeCode 一致：system 按设备系统语言，非 zh/en 回退 zh
Future<String> appLang() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    final code = prefs.getString('locale_code') ?? 'system';
    if (code == 'en') return 'en';
    if (code == 'zh') return 'zh';
  } catch (_) {}
  final sys = PlatformDispatcher.instance.locale.languageCode.toLowerCase();
  return sys.startsWith('en') ? 'en' : 'zh';
}
