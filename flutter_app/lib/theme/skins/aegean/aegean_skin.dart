import 'package:flutter/material.dart';
import '../skin.dart';
import '../skin_colors.dart';
import '../skin_decoration.dart';
import '../skin_typography.dart';
import '../skin_animation.dart';
import 'aegean_palette.dart';

class AegeanSkin implements Skin {
  @override
  String get id => 'aegean';
  @override
  String get displayName => '爱琴海典藏';
  @override
  Color get previewColor => AegeanPalette.goldLight;
  @override
  bool get supportsSeedColor => false; // 固定古金/赤陶，不跟随主题色
  @override
  bool get supportsDarkMode => true;

  @override
  ThemeData buildThemeData({
    required Brightness brightness,
    required Color seedColor, // 本皮肤不使用 seedColor，保留签名以符合接口
  }) {
    final dark = brightness == Brightness.dark;
    final ink = AegeanPalette.ink(brightness);
    final paper = AegeanPalette.paper(brightness);
    final marble = AegeanPalette.marble(brightness);
    final paper2 = AegeanPalette.paper2(brightness);
    final gold = AegeanPalette.gold(brightness);
    final goldDeep = AegeanPalette.goldDeep(brightness);
    final terra = AegeanPalette.terra(brightness);
    final terraDeep = AegeanPalette.terraDeep(brightness);

    // 固定品牌 ColorScheme（fromSeed 仅用来补齐 M3 全套角色，再覆盖关键色）
    // §6.3：M3 五级容器全部显式指定 + 关闭 surfaceTint，杜绝 fromSeed 冷灰容器跳色
    final scheme = ColorScheme.fromSeed(
      seedColor: AegeanPalette.goldDeepLight,
      brightness: brightness,
    ).copyWith(
      primary: goldDeep,
      onPrimary: dark ? AegeanPalette.paperDark : AegeanPalette.cream,
      secondary: terra,
      onSecondary: AegeanPalette.cream,
      tertiary: AegeanPalette.olive,
      onTertiary: Colors.white,
      surface: marble,
      onSurface: ink,
      // M3 五级容器全部显式指定，杜绝 fromSeed 冷灰容器跳色
      surfaceContainerLowest: marble,
      surfaceContainerLow: marble,
      surfaceContainer: paper2,
      surfaceContainerHigh: paper2,
      surfaceContainerHighest: paper2,
      surfaceTint: Colors.transparent, // 关键：关掉 M3 表面 tint
      inverseSurface: dark ? AegeanPalette.marbleLight : AegeanPalette.inkLight,
      onInverseSurface: dark ? AegeanPalette.inkLight : AegeanPalette.marbleLight,
      outline: AegeanPalette.goldPale,
      outlineVariant: AegeanPalette.frameHair,
      error: const Color(0xFFB03A2E),
      onError: Colors.white,
      scrim: const Color(0x991B140C),
      shadow: const Color(0x332B251B),
    );

    // 全量衬线字形（西文走系统 serif / 可选打包花体；中文回退宋体链）
    TextTheme serifText(TextTheme base) {
      return base.apply(
        fontFamily: AegeanPalette.bodyLatinFont,
        bodyColor: ink,
        displayColor: ink,
      ).copyWith(
        // 标题类用花体显示字族 + 略加字重，营造铭牌/手稿标题感
        titleLarge: base.titleLarge?.copyWith(
          fontFamily: AegeanPalette.displayFont,
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.4,
          color: goldDeep,
        ),
        headlineSmall: base.headlineSmall?.copyWith(
          fontFamily: AegeanPalette.displayFont,
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          fontWeight: FontWeight.w700,
          color: ink,
        ),
        labelLarge: base.labelLarge?.copyWith(
          fontFamily: AegeanPalette.bodyLatinFont,
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.6,
        ),
        bodyMedium: base.bodyMedium?.copyWith(
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          height: 1.45,
        ),
        bodyLarge: base.bodyLarge?.copyWith(
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          height: 1.5,
        ),
      );
    }

    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      // 透明 scaffold：让 AppBackground（AegeanPaperPainter 纸感+回纹+金粉）真实可见，
      // 与 glass 皮肤同机制；L1 卡面均由各自主题显式上不透明色。
      scaffoldBackgroundColor: Colors.transparent,
      canvasColor: Colors.transparent,
      dividerColor: AegeanPalette.frameHair,
      textTheme: serifText(
        (dark ? Typography.material2021().white : Typography.material2021().black),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: marble,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: AegeanPalette.frameHair, width: 0.9),
        ),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: paper.withValues(alpha: 0.96),
        foregroundColor: ink,
        centerTitle: true,
        titleTextStyle: TextStyle(
          fontFamily: AegeanPalette.displayFont,
          fontFamilyFallback: AegeanPalette.cnSerifFallback,
          fontSize: 19,
          fontWeight: FontWeight.w700,
          letterSpacing: 1.2,
          color: goldDeep,
        ),
        iconTheme: IconThemeData(color: goldDeep),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: marble,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        height: 70,
        indicatorColor: gold.withValues(alpha: 0.16),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(
          fontSize: 11,
          fontFamily: AegeanPalette.bodyLatinFont,
          color: ink,
        )),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: terraDeep,
          foregroundColor: const Color(0xFFF7EEDD),
          elevation: 0,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
            side: BorderSide(color: gold.withValues(alpha: 0.7), width: 0.8),
          ),
          textStyle: const TextStyle(
            fontWeight: FontWeight.w600,
            letterSpacing: 0.8,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: goldDeep,
          side: BorderSide(color: gold.withValues(alpha: 0.8), width: 0.9),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: terraDeep),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: marble,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: AegeanPalette.frameHair),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: AegeanPalette.frameHair),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: goldDeep, width: 1.4),
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: marble,
        elevation: 8,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: AegeanPalette.frameHair),
        ),
        titleTextStyle: TextStyle(
          fontFamily: AegeanPalette.displayFont,
          color: goldDeep,
          fontSize: 18,
          fontWeight: FontWeight.w700,
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: marble,
        elevation: 0,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(22)),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: paper2,
        side: BorderSide(color: AegeanPalette.frameHair),
        labelStyle: TextStyle(color: ink, fontSize: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
      switchTheme: SwitchThemeData(
        trackOutlineWidth: const WidgetStatePropertyAll(0),
        thumbColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? const Color(0xFFF7EEDD) : null,
        ),
        trackColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? terra : paper2,
        ),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: goldDeep,
        linearTrackColor: paper2,
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: terra,
        inactiveTrackColor: paper2,
        thumbColor: goldDeep,
        overlayColor: gold.withValues(alpha: 0.12),
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: dark ? AegeanPalette.paper2Dark : AegeanPalette.inkLight,
          borderRadius: BorderRadius.circular(6),
        ),
        textStyle: TextStyle(color: dark ? AegeanPalette.inkDark : AegeanPalette.marbleLight, fontSize: 12),
      ),
      dividerTheme: DividerThemeData(
        color: AegeanPalette.frameHair,
        thickness: 0.7,
        space: 0.7,
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: FadeForwardsPageTransitionsBuilder(),
        },
      ),
      // ── §6.3 追加组件主题（一次覆盖所有 M3 件） ──
      // 全局图标：默认用墨色，避免所有图标都变金造成杂乱；主区(AppBar/Nav)单独给金
      iconTheme: IconThemeData(color: ink, size: 22),
      primaryIconTheme: IconThemeData(color: goldDeep, size: 22),
      visualDensity: VisualDensity.comfortable,
      materialTapTargetSize: MaterialTapTargetSize.padded, // 保 44dp 触达
      // 分段选择器（外观页/筛选器大量使用）
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          visualDensity: VisualDensity.compact,
          backgroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected)
                  ? terra.withValues(alpha: 0.16)
                  : marble),
          foregroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected) ? terraDeep : ink),
          side: WidgetStatePropertyAll(
              BorderSide(color: AegeanPalette.frameHair, width: 0.9)),
          shape: WidgetStatePropertyAll(
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(10))),
          textStyle: const WidgetStatePropertyAll(
              TextStyle(fontWeight: FontWeight.w600, letterSpacing: 0.4)),
        ),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: goldDeep,
        unselectedLabelColor: ink.withValues(alpha: 0.62),
        indicatorColor: terra,
        dividerColor: AegeanPalette.goldHair,
        labelStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
        unselectedLabelStyle: const TextStyle(fontSize: 14),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? terraDeep : Colors.transparent),
        checkColor: const WidgetStatePropertyAll(AegeanPalette.cream),
        side: const BorderSide(color: AegeanPalette.goldPale, width: 1.2),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
      ),
      radioTheme: RadioThemeData(
        fillColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? goldDeep : AegeanPalette.goldPale),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: terraDeep,
        foregroundColor: AegeanPalette.cream,
        elevation: 2,
        focusElevation: 2,
        hoverElevation: 3,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: gold.withValues(alpha: 0.7), width: 0.9),
        ),
      ),
      badgeTheme: BadgeThemeData(
        backgroundColor: terra,
        textColor: AegeanPalette.cream,
        textStyle: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: dark ? AegeanPalette.inkBlockDark : AegeanPalette.inkBlockLight,
        contentTextStyle: TextStyle(color: dark ? AegeanPalette.inkDark : AegeanPalette.marbleLight, fontSize: 13.5),
        actionTextColor: AegeanPalette.goldPale,
        elevation: 8,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(10),
          side: BorderSide(color: AegeanPalette.goldPale.withValues(alpha: 0.5)),
        ),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: marble,
        surfaceTintColor: Colors.transparent,
        shadowColor: const Color(0x332B251B),
        textStyle: TextStyle(color: ink, fontSize: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: AegeanPalette.frameHair, width: 0.9),
        ),
      ),
      menuTheme: MenuThemeData(
        style: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(marble),
          surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
          shape: WidgetStatePropertyAll(RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: BorderSide(color: AegeanPalette.frameHair, width: 0.9),
          )),
        ),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        menuStyle: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(marble),
          surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
          shape: WidgetStatePropertyAll(RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: BorderSide(color: AegeanPalette.frameHair, width: 0.9),
          )),
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: goldDeep,
        textColor: ink,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
      expansionTileTheme: ExpansionTileThemeData(
        iconColor: goldDeep,
        collapsedIconColor: ink.withValues(alpha: 0.7),
        textColor: goldDeep,
        collapsedTextColor: ink,
        shape: const Border(),
        collapsedShape: const Border(),
      ),
      textSelectionTheme: TextSelectionThemeData(
        cursorColor: terraDeep,
        selectionColor: gold.withValues(alpha: 0.28),
        selectionHandleColor: terra,
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStatePropertyAll(gold.withValues(alpha: 0.3)),
        radius: const Radius.circular(4),
        thickness: const WidgetStatePropertyAll(5),
      ),
      drawerTheme: DrawerThemeData(
        backgroundColor: paper2,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          right: BorderSide(color: AegeanPalette.frameHair, width: 1.2),
        ),
      ),
      bottomAppBarTheme: BottomAppBarThemeData(
        color: marble,
        surfaceTintColor: Colors.transparent,
        shadowColor: const Color(0x222B251B),
      ),
    );
    return base;
  }

  @override
  SkinColors buildSkinColors({
    required Brightness brightness,
    required Color seedColor,
  }) {
    final marble = AegeanPalette.marble(brightness);
    final paper = AegeanPalette.paper(brightness);
    final paper2 = AegeanPalette.paper2(brightness);
    final terra = AegeanPalette.terra(brightness);
    final ink = AegeanPalette.ink(brightness);
    return SkinColors(
      // 用户气泡：赤陶 + 米白字；AI 气泡：羊皮卷 + 墨字
      bubbleUser: terra,
      bubbleAi: marble,
      bubbleUserText: const Color(0xFFF7EEDD),
      bubbleAiText: ink,
      glassBackground: marble.withValues(alpha: 0.92),
      glassBorder: AegeanPalette.goldPale.withValues(alpha: 0.55),
      glassBlur: 6, // 纸感为主，轻模糊即可，省 GPU
      cardElevated: marble,
      quoteBarBg: paper2,
      inputBarBg: marble,
      bgGradientStart: paper,
      bgGradientEnd: paper2,
      auroraColor1: AegeanPalette.goldPale.withValues(alpha: 0.18),
      auroraColor2: AegeanPalette.terra(brightness).withValues(alpha: 0.10),
      backgroundBlur: 0,
      backgroundDim: 0,
    );
  }

  @override
  SkinDecoration get decoration => SkinDecoration(
        cardRadius: 14,
        buttonRadius: 10,
        inputRadius: 12,
        navBarStyle: NavigationBarStyle.floatingCapsule,
        appBarBlur: false,
        cardBorder: true,
        borderColor: AegeanPalette.frameHair,
        borderWidth: 0.9,
        cardShadow: [
          // 暖金柔光 + 极轻投影，营造纸卡浮于纸面
          BoxShadow(
            color: const Color(0xFF836428).withValues(alpha: 0.12),
            blurRadius: 18,
            offset: const Offset(0, 6),
          ),
          BoxShadow(
            color: const Color(0xFF2B251B).withValues(alpha: 0.04),
            blurRadius: 4,
            offset: const Offset(0, 1),
          ),
        ],
      );

  @override
  SkinTypography get typography => SkinTypography.serif;

  @override
  SkinAnimation get animation => SkinAnimation.elastic;
}
