import "package:flutter/material.dart";

/// 主题系统 v2（UI 2.0 iOS 风格视觉基础）：
/// - 浅色：灰底 #F2F2F7 + 白色圆角卡片 + 无阴影 AppBar/NavigationBar
/// - 深色：深底 + 深色卡片，保持可读性
/// 色板值全局唯一来源，SettingsProvider 只存索引。
class AppTheme {
  static const List<String> seedNames = ['蓝', '紫', '粉', '青', '绿', '橙'];
  static const List<Color> seedColors = [
    Colors.blue,
    Colors.deepPurple,
    Colors.pink,
    Colors.teal,
    Colors.green,
    Colors.orange,
  ];

  static const Color lightBg = Color(0xFFF2F2F7);
  static const Color lightCard = Color(0xFFFFFFFF);
  static const Color lightDivider = Color(0xFFECECEF);
  static const Color darkBg = Color(0xFF0E0E10);
  static const Color darkCard = Color(0xFF1C1C1E);
  static const Color darkDivider = Color(0xFF2C2C2E);

  static ThemeData light(int seedIndex) {
    final base = ThemeData(
      colorSchemeSeed: seedColors[seedIndex.clamp(0, seedColors.length - 1)],
      useMaterial3: true,
      brightness: Brightness.light,
    );
    return base.copyWith(
      scaffoldBackgroundColor: lightBg,
      canvasColor: lightBg,
      dividerColor: lightDivider,
      cardTheme: CardThemeData(
        elevation: 0,
        color: lightCard,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      appBarTheme: const AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: lightBg,
        foregroundColor: Color(0xFF1C1C1E),
        titleTextStyle: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: Color(0xFF1C1C1E)),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: lightCard,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 64,
      ),
      switchTheme: SwitchThemeData(
        trackOutlineWidth: const WidgetStatePropertyAll(0),
        trackColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected) ? null : const Color(0xFFE9E9EB),
        ),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: CupertinoPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        },
      ),
    );
  }

  static ThemeData dark(int seedIndex) {
    final base = ThemeData(
      colorSchemeSeed: seedColors[seedIndex.clamp(0, seedColors.length - 1)],
      useMaterial3: true,
      brightness: Brightness.dark,
    );
    return base.copyWith(
      scaffoldBackgroundColor: darkBg,
      canvasColor: darkBg,
      dividerColor: darkDivider,
      cardTheme: CardThemeData(
        elevation: 0,
        color: darkCard,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: darkBg,
        foregroundColor: Colors.white,
        titleTextStyle: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: Colors.white),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: darkCard,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 64,
      ),
      switchTheme: SwitchThemeData(
        trackOutlineWidth: const WidgetStatePropertyAll(0),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: CupertinoPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        },
      ),
    );
  }

  /// 0=跟随系统 1=浅色 2=深色
  static ThemeMode modeFromIndex(int index) {
    switch (index) {
      case 1:
        return ThemeMode.light;
      case 2:
        return ThemeMode.dark;
      default:
        return ThemeMode.system;
    }
  }

  static String modeName(int index) {
    switch (index) {
      case 1:
        return '浅色';
      case 2:
        return '深色';
      default:
        return '跟随系统';
    }
  }
}
