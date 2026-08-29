import "package:flutter/material.dart";
import "skins/skin.dart";
import "skins/skin_registry.dart";
import "skins/skin_colors.dart";
import "tokens.dart";

/// 主题系统 v3（可换肤架构）：
///
/// 兼容现有调用方式（`AppTheme.light(seedIndex)` / `AppTheme.dark(seedIndex)`），
/// 但内部已接入 [SkinRegistry] 皮肤包体系。
///
/// 新代码推荐直接用：
/// ```dart
/// final skin = SkinRegistry.get(settings.skinId);
/// final theme = skin.buildThemeData(brightness: brightness, seedColor: seedColor);
/// ```
class AppTheme {
  /// 6 款强调色（所有皮肤共享的 seed color 池）
  static const List<Color> seedColors = [
    Colors.blue,
    Colors.deepPurple,
    Colors.pink,
    Colors.teal,
    Colors.green,
    Colors.orange,
  ];

  /// 取第 index 个 seed color（安全 clamp）
  static Color seedColorAt(int index) =>
      seedColors[index.clamp(0, seedColors.length - 1)];

  // ─── 兼容旧 API（内部转发到 SkinRegistry） ───

  static ThemeData light(int seedIndex, {String skinId = SkinRegistry.defaultSkinId}) {
    return _build(Brightness.light, seedIndex, skinId);
  }

  static ThemeData dark(int seedIndex, {String skinId = SkinRegistry.defaultSkinId}) {
    return _build(Brightness.dark, seedIndex, skinId);
  }

  static ThemeData _build(Brightness brightness, int seedIndex, String skinId) {
    // 深色模式回退：皮肤声明不支持深色时，深色主题使用默认 ios 皮肤
    // （用户在浅色下看到 paper，深色下自动回退 ios，避免纸色配深色文字的对比灾难）
    Skin skin = SkinRegistry.get(skinId);
    if (brightness == Brightness.dark && !skin.supportsDarkMode && skinId != SkinRegistry.defaultSkinId) {
      skin = SkinRegistry.get(SkinRegistry.defaultSkinId);
    }
    final seed = seedColorAt(seedIndex);
    final theme = skin.buildThemeData(brightness: brightness, seedColor: seed);
    final SkinColors skinColors = skin.buildSkinColors(brightness: brightness, seedColor: seed);
    return theme.copyWith(
      extensions: [skinColors],
    );
  }

  /// 新 API：直接按 skinId + seedIndex + brightness 构建完整 ThemeData
  static ThemeData build({
    required Brightness brightness,
    required int seedIndex,
    String skinId = SkinRegistry.defaultSkinId,
  }) {
    return _build(brightness, seedIndex, skinId);
  }

  // ─── 旧版色值常量（兼容现有页面直接引用） ───

  static const Color lightBg = AppColors.bgLight;
  static const Color lightCard = AppColors.cardLight;
  static const Color lightDivider = AppColors.dividerLight;
  static const Color darkBg = AppColors.bgDark;
  static const Color darkCard = AppColors.cardDark;
  static const Color darkDivider = AppColors.dividerDark;

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
}
