import "package:flutter/material.dart";
import "tokens.dart";

/// 主题系统 v2（UI 2.0 iOS 风格视觉基础）：
/// - 浅色：灰底 #F2F2F7 + 白色圆角卡片 + 无阴影 AppBar/NavigationBar
/// - 深色：深底 + 深色卡片，保持可读性
/// 色板值全局唯一来源，SettingsProvider 只存索引；色值原子引自 tokens.dart（AppColors 等）。
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

  static const Color lightBg = AppColors.bgLight;
  static const Color lightCard = AppColors.cardLight;
  static const Color lightDivider = AppColors.dividerLight;
  static const Color darkBg = AppColors.bgDark;
  static const Color darkCard = AppColors.cardDark;
  static const Color darkDivider = AppColors.dividerDark;

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
        foregroundColor: AppColors.textPrimary,
        titleTextStyle: TextStyle(fontSize: AppTypography.titleSize, fontWeight: AppTypography.titleWeight, color: AppColors.textPrimary),
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
          (states) => states.contains(WidgetState.selected) ? null : AppColors.surfaceAlt,
        ),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: FadeForwardsPageTransitionsBuilder(),
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
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: FadeForwardsPageTransitionsBuilder(),
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
