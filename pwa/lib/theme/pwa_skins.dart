/// PWA 外观预设（2026-11-22 外观批）：皮肤配色预设 ＋ 主题色 seed。
///
/// 与 App 皮肤体系的关系：App 皮肤 = 完整主题构建器（背景装饰/组件皮肤/动画/字体
/// 全套，`flutter_app/lib/theme/skins/`）；PWA（Web 工作台）采用「配色皮肤预设」——
/// **对齐 App 的 7 个皮肤 id**（ios/warm/material/paper/neon/glass/aegean）与
/// **6 款主题色 seed**（蓝/紫/粉/青/绿/橙），但装饰层/动画层/字体变体不同构
/// （Web 本地仅 NotoSansSC 一种字体，变体不可用——见 parity 台账 ◐ 口径）。
///
/// 纪律：外观为**会话内存态**（Web 不落 localStorage/缓存/URL，刷新回默认），
/// 与登录 token 同口径。
library;

import 'package:flutter/material.dart';

class PwaSkinPreset {
  const PwaSkinPreset({
    required this.id,
    required this.nameZh,
    required this.nameEn,
    required this.seed,
    required this.canvasLight,
    required this.canvasDark,
    required this.panelLight,
    required this.panelDark,
    required this.lineLight,
    required this.lineDark,
    this.railBgLight,
    this.railBgDark,
    this.indicatorLight,
    this.indicatorDark,
  });

  final String id;
  final String nameZh;
  final String nameEn;

  /// 皮肤默认 seed（被「主题色」覆盖——选了主题色则用主题色 seed）。
  final Color seed;

  final Color canvasLight;
  final Color canvasDark;
  final Color panelLight;
  final Color panelDark;
  final Color lineLight;
  final Color lineDark;

  /// 导航栏底色（缺省＝canvas 加微弱明暗偏移派生）。
  final Color? railBgLight;
  final Color? railBgDark;

  /// 导航选中指示（缺省＝scheme.primary 派生；浅色档配白色选中图标）。
  final Color? indicatorLight;
  final Color? indicatorDark;
}

/// 7 款皮肤预设（id 与 `flutter_app/lib/theme/skins/skin_registry.dart` 对齐）。
/// 默认 `ios`＝PWA 现有暖白工作台观感（画布 F6F4EF／面板 FFFDF9／线 E2DED5，
/// 导航栏与指示色带原值覆写，保证默认观感逐字节不漂）。
const List<PwaSkinPreset> pwaSkinPresets = [
  PwaSkinPreset(
    id: 'ios',
    nameZh: 'iOS',
    nameEn: 'iOS',
    seed: Color(0xFF4C6657),
    canvasLight: Color(0xFFF6F4EF),
    canvasDark: Color(0xFF0B0F14),
    panelLight: Color(0xFFFFFDF9),
    panelDark: Color(0xFF10161E),
    lineLight: Color(0xFFE2DED5),
    lineDark: Color(0xFF24313F),
    railBgLight: Color(0xFFF0EDE6),
    railBgDark: Color(0xFF0E141B),
    indicatorLight: Color(0xFF211F1B),
    indicatorDark: Color(0xFF23313E),
  ),
  PwaSkinPreset(
    id: 'warm',
    nameZh: '暖白',
    nameEn: 'Warm',
    seed: Color(0xFFB0724A),
    canvasLight: Color(0xFFFDF6EE),
    canvasDark: Color(0xFF141210),
    panelLight: Color(0xFFFFF9F0),
    panelDark: Color(0xFF1C1814),
    lineLight: Color(0xFFEADFCF),
    lineDark: Color(0xFF2A241E),
  ),
  PwaSkinPreset(
    id: 'material',
    nameZh: 'Material',
    nameEn: 'Material',
    seed: Color(0xFF2196F3),
    canvasLight: Color(0xFFFAFAFA),
    canvasDark: Color(0xFF121212),
    panelLight: Color(0xFFFFFFFF),
    panelDark: Color(0xFF1E1E1E),
    lineLight: Color(0xFFE0E0E0),
    lineDark: Color(0xFF2C2C2C),
  ),
  PwaSkinPreset(
    id: 'paper',
    nameZh: '纸色',
    nameEn: 'Paper',
    seed: Color(0xFFB8860B),
    canvasLight: Color(0xFFF7F1E3),
    canvasDark: Color(0xFF17140E),
    panelLight: Color(0xFFFFFBEF),
    panelDark: Color(0xFF1F1B14),
    lineLight: Color(0xFFE8DFC8),
    lineDark: Color(0xFF332C1E),
  ),
  PwaSkinPreset(
    id: 'neon',
    nameZh: '霓虹',
    nameEn: 'Neon',
    seed: Color(0xFF7C4DFF),
    canvasLight: Color(0xFFF4F3FB),
    canvasDark: Color(0xFF0F0F1E),
    panelLight: Color(0xFFFBFAFF),
    panelDark: Color(0xFF16162A),
    lineLight: Color(0xFFDCD9F0),
    lineDark: Color(0xFF2A2A44),
  ),
  PwaSkinPreset(
    id: 'glass',
    nameZh: '玻璃',
    nameEn: 'Glass',
    seed: Color(0xFF607D8B),
    canvasLight: Color(0xFFEFF3F5),
    canvasDark: Color(0xFF0E1417),
    panelLight: Color(0xFFF7FAFB),
    panelDark: Color(0xFF151C20),
    lineLight: Color(0xFFD8E0E4),
    lineDark: Color(0xFF242E34),
  ),
  PwaSkinPreset(
    id: 'aegean',
    nameZh: '爱琴海',
    nameEn: 'Aegean',
    seed: Color(0xFF1E6B8C),
    canvasLight: Color(0xFFEFF5F8),
    canvasDark: Color(0xFF0A1218),
    panelLight: Color(0xFFF8FBFD),
    panelDark: Color(0xFF111A22),
    lineLight: Color(0xFFD5E3EB),
    lineDark: Color(0xFF1F2C36),
  ),
];

PwaSkinPreset pwaSkinById(String id) =>
    pwaSkinPresets.firstWhere((s) => s.id == id,
        orElse: () => pwaSkinPresets.first);

/// 主题色 6 款（与 `AppTheme.seedColors` 对齐：蓝/紫/粉/青/绿/橙）。
const List<({Color color, String nameZh, String nameEn})> pwaSeedColors = [
  (color: Color(0xFF2196F3), nameZh: '蓝', nameEn: 'Blue'),
  (color: Color(0xFF673AB7), nameZh: '紫', nameEn: 'Purple'),
  (color: Color(0xFFE91E63), nameZh: '粉', nameEn: 'Pink'),
  (color: Color(0xFF009688), nameZh: '青', nameEn: 'Teal'),
  (color: Color(0xFF4CAF50), nameZh: '绿', nameEn: 'Green'),
  (color: Color(0xFFFF9800), nameZh: '橙', nameEn: 'Orange'),
];

/// 工作台主题构建：seed = 主题色（未选时回落皮肤默认 seed）；
/// 画布/面板/线/导航栏/指示按皮肤预设取值。iOS 预设带原值覆写 ⇒ 默认观感不漂。
ThemeData pwaWorkbenchTheme(Brightness brightness, PwaSkinPreset skin,
    {Color? accentSeed}) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: accentSeed ?? skin.seed,
    brightness: brightness,
  );
  final canvas = dark ? skin.canvasDark : skin.canvasLight;
  final panel = dark ? skin.panelDark : skin.panelLight;
  final line = dark ? skin.lineDark : skin.lineLight;
  final railBg = dark
      ? (skin.railBgDark ??
          Color.alphaBlend(Colors.white.withValues(alpha: 0.04), canvas))
      : (skin.railBgLight ??
          Color.alphaBlend(Colors.black.withValues(alpha: 0.05), canvas));
  final indicator = dark
      ? (skin.indicatorDark ?? scheme.primary.withValues(alpha: 0.30))
      : (skin.indicatorLight ?? scheme.primary);
  return ThemeData(
    useMaterial3: true,
    fontFamily: 'NotoSansSC',
    colorScheme: scheme,
    scaffoldBackgroundColor: canvas,
    dividerColor: line,
    dividerTheme: DividerThemeData(color: line, thickness: 1, space: 1),
    appBarTheme: AppBarTheme(
      backgroundColor: panel,
      foregroundColor: scheme.onSurface,
      elevation: 0,
      surfaceTintColor: Colors.transparent,
      shape: Border(bottom: BorderSide(color: line)),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: railBg,
      indicatorColor: indicator,
      selectedIconTheme: IconThemeData(
          color: dark ? scheme.primary : Colors.white),
      unselectedIconTheme: IconThemeData(color: scheme.onSurfaceVariant),
      selectedLabelTextStyle: TextStyle(
          fontFamily: 'NotoSansSC',
          color: scheme.onSurface,
          fontSize: 10,
          fontWeight: FontWeight.w600),
      unselectedLabelTextStyle: TextStyle(
          fontFamily: 'NotoSansSC', color: scheme.onSurfaceVariant, fontSize: 10),
      minWidth: 76,
    ),
    inputDecorationTheme: InputDecorationTheme(
      isDense: true,
      filled: true,
      fillColor: canvas,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(7),
        borderSide: BorderSide(color: line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(7),
        borderSide: BorderSide(color: line),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(7),
        borderSide: BorderSide(color: scheme.primary),
      ),
    ),
    cardTheme: CardThemeData(
      color: panel,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(7),
        side: BorderSide(color: line),
      ),
    ),
  );
}
