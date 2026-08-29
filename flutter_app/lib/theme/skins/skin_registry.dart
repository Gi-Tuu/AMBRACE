import 'package:flutter/material.dart';
import 'skin.dart';
import 'skin_colors.dart';
import 'skin_decoration.dart';
import 'skin_typography.dart';
import 'skin_animation.dart';
import '../tokens.dart';

/// AMBRACE 皮肤注册表 —— 所有内置皮肤的唯一入口。
///
/// 使用方式：
/// ```dart
/// final skin = SkinRegistry.get(settings.skinId);
/// final theme = skin.buildThemeData(brightness: brightness, seedColor: seedColor);
/// ```
///
/// 如需热更新/服务器下发皮肤，可扩展 [registerRemoteSkin] 方法。
class SkinRegistry {
  SkinRegistry._();

  static final _skins = <String, Skin>{};
  static bool _initialized = false;

  /// 默认皮肤 ID（与现有逻辑兼容：原生 iOS）
  static const String defaultSkinId = 'ios';

  /// 初始化注册所有内置皮肤（在 main.dart 中调用一次）
  static void initialize() {
    if (_initialized) return;
    _initialized = true;

    // 内置皮肤注册顺序 = 设置页展示顺序
    _register(_IosSkin());
    _register(_WarmSkin());
    _register(_MaterialSkin());
    _register(_PaperSkin());
    _register(_NeonSkin());
    _register(_GlassSkin());
  }

  static void _register(Skin skin) {
    _skins[skin.id] = skin;
  }

  /// 按 ID 取皮肤（fallback 到默认）。未初始化时先懒加载注册内置皮肤。
  static Skin get(String id) {
    if (!_initialized) initialize();
    return _skins[id] ?? _skins[defaultSkinId]!;
  }

  /// 所有已注册皮肤（有序）
  static List<Skin> get all => _initThen(() => List.unmodifiable(_skins.values));

  /// 皮肤 ID 列表
  static List<String> get ids => _initThen(() => List.unmodifiable(_skins.keys));

  /// 是否存在某皮肤
  static bool has(String id) => _initThen(() => _skins.containsKey(id));

  static T _initThen<T>(T Function() f) {
    if (!_initialized) initialize();
    return f();
  }

  // ───────────────────────────────────────────────
  // 内置皮肤实现（私有类，不暴露细节）
  // ───────────────────────────────────────────────
}

// ═══════════════════════════════════════════════════
//  1. 原生 iOS（兼容现有 UI 2.0）
// ═══════════════════════════════════════════════════
class _IosSkin implements Skin {
  @override String get id => 'ios';
  @override String get displayName => '原生态';
  @override Color get previewColor => AppColors.accent;
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    final base = ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: brightness,
    );
    return base.copyWith(
      scaffoldBackgroundColor: isDark ? AppColors.bgDark : AppColors.bgLight,
      canvasColor: isDark ? AppColors.bgDark : AppColors.bgLight,
      dividerColor: isDark ? AppColors.dividerDark : AppColors.dividerLight,
      cardTheme: CardThemeData(
        elevation: 0,
        color: isDark ? AppColors.cardDark : AppColors.cardLight,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: isDark ? AppColors.bgDark : AppColors.bgLight,
        foregroundColor: isDark ? Colors.white : AppColors.textPrimary,
        titleTextStyle: TextStyle(
          fontSize: 17, fontWeight: FontWeight.w600,
          color: isDark ? Colors.white : AppColors.textPrimary,
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: isDark ? AppColors.cardDark : AppColors.cardLight,
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

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    return SkinColors(
      bubbleUser: seedColor,
      bubbleAi: isDark ? const Color(0xFF2C2C2E) : const Color(0xFFF2F2F7),
      bubbleUserText: Colors.white,
      bubbleAiText: isDark ? Colors.white : AppColors.textPrimary,
      glassBackground: null,
      quoteBarBg: isDark ? const Color(0xFF2C2C2E) : const Color(0xFFECECEF),
      inputBarBg: isDark ? const Color(0xFF1C1C1E) : Colors.white,
    );
  }

  @override SkinDecoration get decoration => const SkinDecoration(
    cardRadius: 16, buttonRadius: 12, inputRadius: 12,
    navBarStyle: NavigationBarStyle.fullWidth,
  );
  @override SkinTypography get typography => SkinTypography.system;
  @override SkinAnimation get animation => SkinAnimation.standard;
}

// ═══════════════════════════════════════════════════
//  2. 温柔陪伴（毛玻璃暖调）⭐ 推荐首做
// ═══════════════════════════════════════════════════
class _WarmSkin implements Skin {
  @override String get id => 'warm';
  @override String get displayName => '温柔陪伴';
  @override Color get previewColor => const Color(0xFFFFAFA3);
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    final bg = isDark ? const Color(0xFF1A1518) : const Color(0xFFFAF5F0);
    final card = isDark ? const Color(0xFF2A2026).withAlpha(180) : const Color(0xE6FFFFFF);

    final base = ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: brightness,
    );
    return base.copyWith(
      scaffoldBackgroundColor: bg,
      canvasColor: bg,
      dividerColor: isDark ? const Color(0xFF3A3036) : const Color(0xFFE8E0DA),
      cardTheme: CardThemeData(
        elevation: 0,
        color: card,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: bg,
        foregroundColor: isDark ? Colors.white : AppColors.textPrimary,
        titleTextStyle: TextStyle(
          fontSize: 18, fontWeight: FontWeight.w600,
          color: isDark ? Colors.white : AppColors.textPrimary,
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: card,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 72,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: FadeForwardsPageTransitionsBuilder(),
        },
      ),
    );
  }

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    return SkinColors(
      bubbleUser: seedColor.withAlpha(220),
      bubbleAi: isDark ? const Color(0xB32A2026) : const Color(0xE6FFFFFF),
      bubbleUserText: Colors.white,
      bubbleAiText: isDark ? Colors.white : AppColors.textPrimary,
      glassBackground: isDark ? const Color(0x802A2026) : const Color(0xB3FFFFFF),
      glassBorder: isDark ? const Color(0x30FFFFFF) : const Color(0x20FFFFFF),
      glassBlur: 16,
      cardElevated: isDark ? const Color(0xFF352A32) : const Color(0xFFFFFFFF),
      quoteBarBg: isDark ? const Color(0x802A2026) : const Color(0x80E8E0DA),
      inputBarBg: isDark ? const Color(0xB31A1518) : const Color(0xE6FFFFFF),
      bgGradientStart: isDark ? const Color(0xFF1A1518) : const Color(0xFFFAF5F0),
      bgGradientEnd: isDark ? const Color(0xFF221A20) : const Color(0xFFF0EBF5),
    );
  }

  @override SkinDecoration get decoration => const SkinDecoration(
    cardRadius: 20,
    buttonRadius: 16,
    inputRadius: 16,
    navBarStyle: NavigationBarStyle.floatingCapsule,
    appBarBlur: true,
    cardShadow: [BoxShadow(color: Color(0x0D000000), blurRadius: 12, offset: Offset(0, 4))],
  );
  @override SkinTypography get typography => SkinTypography.rounded;
  @override SkinAnimation get animation => SkinAnimation.elastic;
}

// ═══════════════════════════════════════════════════
//  3. Material You（动态取色）
// ═══════════════════════════════════════════════════
class _MaterialSkin implements Skin {
  @override String get id => 'material';
  @override String get displayName => 'Material You';
  @override Color get previewColor => const Color(0xFF6750A4);
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    return ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: brightness,
    );
  }

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    return const SkinColors(); // Material3 ColorScheme 已足够
  }

  @override SkinDecoration get decoration => const SkinDecoration(
    cardRadius: 12,
    buttonRadius: 20,
    inputRadius: 4,
    navBarStyle: NavigationBarStyle.fullWidth,
  );
  @override SkinTypography get typography => SkinTypography.system;
  @override SkinAnimation get animation => SkinAnimation.standard;
}

// ═══════════════════════════════════════════════════
//  4. 纸艺手账（拟物轻复古）
// ═══════════════════════════════════════════════════
class _PaperSkin implements Skin {
  @override String get id => 'paper';
  @override String get displayName => '纸艺手账';
  @override Color get previewColor => const Color(0xFFD4A574);
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => false; // 纸艺仅浅色

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    const bg = Color(0xFFFDF8F0);
    const card = Color(0xFFFFFFFF);

    final base = ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: Brightness.light,
    );
    return base.copyWith(
      scaffoldBackgroundColor: bg,
      canvasColor: bg,
      dividerColor: const Color(0xFFE0D5C8),
      cardTheme: const CardThemeData(
        elevation: 0,
        color: card,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.only(
            topLeft: Radius.circular(2),
            topRight: Radius.circular(12),
            bottomLeft: Radius.circular(12),
            bottomRight: Radius.circular(2),
          ),
        ),
      ),
      appBarTheme: const AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: bg,
        foregroundColor: Color(0xFF3D3229),
        titleTextStyle: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: Color(0xFF3D3229)),
      ),
      navigationBarTheme: const NavigationBarThemeData(
        backgroundColor: card,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 64,
      ),
    );
  }

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    return const SkinColors(
      bubbleUser: Color(0xFFFFF3E0),
      bubbleAi: Color(0xFFFFFFFF),
      bubbleUserText: Color(0xFF3D3229),
      bubbleAiText: Color(0xFF3D3229),
      quoteBarBg: Color(0xFFF5EDE3),
      inputBarBg: Color(0xFFFFFFFF),
    );
  }

  @override SkinDecoration get decoration => const SkinDecoration(
    cardRadius: 12,
    buttonRadius: 8,
    inputRadius: 8,
    navBarStyle: NavigationBarStyle.fullWidth,
    cardBorder: true,
    borderColor: Color(0xFFE0D5C8),
    borderWidth: 0.8,
    cardShadow: [BoxShadow(color: Color(0x1A5C4033), blurRadius: 4, offset: Offset(1, 2))],
  );
  @override SkinTypography get typography => SkinTypography.serif;
  @override SkinAnimation get animation => SkinAnimation.standard;
}

// ═══════════════════════════════════════════════════
//  5. 暗夜霓虹（深色科技）
// ═══════════════════════════════════════════════════
class _NeonSkin implements Skin {
  @override String get id => 'neon';
  @override String get displayName => '暗夜霓虹';
  @override Color get previewColor => const Color(0xFF00F0FF);
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    const bg = Color(0xFF050508);
    const card = Color(0xFF0E0E14);

    final base = ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: Brightness.dark,
    );
    return base.copyWith(
      scaffoldBackgroundColor: bg,
      canvasColor: bg,
      dividerColor: const Color(0xFF1E1E2E),
      cardTheme: CardThemeData(
        elevation: 0,
        color: card,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: BorderSide(color: seedColor.withAlpha(60), width: 1),
        ),
      ),
      appBarTheme: const AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: bg,
        foregroundColor: Colors.white,
        titleTextStyle: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: Colors.white),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: card,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 64,
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: ZoomPageTransitionsBuilder(),
          TargetPlatform.iOS: ZoomPageTransitionsBuilder(),
        },
      ),
    );
  }

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    return SkinColors(
      bubbleUser: seedColor.withAlpha(40),
      bubbleAi: const Color(0xFF141420),
      bubbleUserText: seedColor,
      bubbleAiText: Colors.white,
      glassBackground: const Color(0x80141420),
      glassBorder: seedColor.withAlpha(80),
      cardElevated: const Color(0xFF1A1A28),
      quoteBarBg: const Color(0xFF141420),
      inputBarBg: const Color(0xFF0E0E14),
    );
  }

  @override SkinDecoration get decoration => SkinDecoration(
    cardRadius: 8,
    buttonRadius: 6,
    inputRadius: 6,
    navBarStyle: NavigationBarStyle.fullWidth,
    cardShadow: [
      BoxShadow(
        color: const Color(0xFF00F0FF).withAlpha(40),  // #63: decoration getter 无 seedColor 入参，用霓虹默认色
        blurRadius: 8,
        offset: const Offset(0, 0),
      ),
    ],
  );
  @override SkinTypography get typography => SkinTypography.system;
  @override SkinAnimation get animation => SkinAnimation.snappy;
}

// ═══════════════════════════════════════════════════
//  6. 极光毛玻璃（Aurora Glass）
// ═══════════════════════════════════════════════════
class _GlassSkin implements Skin {
  @override String get id => 'glass';
  @override String get displayName => '极光毛玻璃';
  @override Color get previewColor => const Color(0xFF82B1FF);
  @override bool get supportsSeedColor => true;
  @override bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    final base = ThemeData(
      colorSchemeSeed: seedColor,
      useMaterial3: true,
      brightness: brightness,
    );
    return base.copyWith(
      // 透明背景：让 AppBackground 层可见
      scaffoldBackgroundColor: Colors.transparent,
      canvasColor: Colors.transparent,
      dividerColor: isDark ? const Color(0x30FFFFFF) : const Color(0x15000000),
      cardTheme: CardThemeData(
        elevation: 0,
        color: isDark ? const Color(0x331C1C1E) : const Color(0x66FFFFFF),
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: isDark ? const Color(0x4D1C1C1E) : const Color(0x55FFFFFF),
        foregroundColor: isDark ? Colors.white : AppColors.textPrimary,
        titleTextStyle: TextStyle(
          fontSize: 17, fontWeight: FontWeight.w600,
          color: isDark ? Colors.white : AppColors.textPrimary,
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: isDark ? const Color(0x4D1C1C1E) : const Color(0x55FFFFFF),
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 64,
      ),
      // #14 FadeForwards 在 pop 时会在两层路由间垫一块不明白色基底 → 玻璃皮肤返回短暂白屏；
      // 改用 FadeUpwards（两层叠放、无白色基底）。
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeUpwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: FadeUpwardsPageTransitionsBuilder(),
        },
      ),
    );
  }

  @override
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor}) {
    final isDark = brightness == Brightness.dark;
    return SkinColors(
      bubbleUser: seedColor.withAlpha(180),
      bubbleAi: isDark ? const Color(0x402C2C2E) : const Color(0x55FFFFFF),
      bubbleUserText: Colors.white,
      bubbleAiText: isDark ? Colors.white : AppColors.textPrimary,
      glassBackground: isDark ? const Color(0x401C1C1E) : const Color(0x55FFFFFF),
      glassBorder: isDark ? const Color(0x30FFFFFF) : const Color(0x20000000),
      glassBlur: 20,
      cardElevated: isDark ? const Color(0x552C2C2E) : const Color(0x80FFFFFF),
      quoteBarBg: isDark ? const Color(0x402C2C2E) : const Color(0x55ECECEF),
      inputBarBg: isDark ? const Color(0x4D1C1C1E) : const Color(0x66FFFFFF),
      auroraColor1: seedColor.withAlpha(40),
      auroraColor2: seedColor.withAlpha(15),
      backgroundBlur: 15,
      backgroundDim: isDark ? 0.35 : 0.1,
    );
  }

  @override SkinDecoration get decoration => const SkinDecoration(
    cardRadius: 20,
    buttonRadius: 16,
    inputRadius: 16,
    navBarStyle: NavigationBarStyle.floatingCapsule,
    appBarBlur: true,
    cardShadow: [BoxShadow(color: Color(0x0D000000), blurRadius: 12, offset: Offset(0, 4))],
  );
  @override SkinTypography get typography => SkinTypography.rounded;
  @override SkinAnimation get animation => SkinAnimation.elastic;
}
