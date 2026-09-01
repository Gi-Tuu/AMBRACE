/// AMBRACE 设计系统统一出口（F7，2026-08-31）。
///
/// 沉淀 Aurora 批次反复出现的卡片/玻璃栏/浮层/骨架/空态范式：既有规范件留在
/// lib/widgets/ 原位（单一实现源），本桶只做统一命名出口 + 两个补位新件
/// （GlassScaffold 玻璃页骨架 / StateBadge 状态徽标）。
/// 新页面一律 `import 'package:ai_companion/core/design_system.dart';`，
/// 禁止再造平行卡片/空态组件（防双轨）。
library;

export 'package:ai_companion/widgets/app_background.dart' show AppBackground;
export 'package:ai_companion/widgets/aurora_card.dart' show AuroraCard;
export 'package:ai_companion/widgets/empty_state.dart' show EmptyState;
export 'package:ai_companion/widgets/entrance_fade.dart' show EntranceFade;
export 'package:ai_companion/widgets/floating_sheet.dart' show FloatingSheet, showFloatingSheet;
export 'package:ai_companion/widgets/glass_bar.dart' show GlassBar, GlassBarBorder;
export 'package:ai_companion/widgets/shimmer.dart'
    show Shimmer, SkeletonBox, SkeletonCircle, SkeletonLine;

// export 不会把名字带给本库：新件（GlassScaffold/别名）需要显式 import 同一实现
import 'package:ai_companion/widgets/aurora_card.dart' show AuroraCard;
import 'package:ai_companion/widgets/floating_sheet.dart' show FloatingSheet;
import 'package:ai_companion/widgets/glass_bar.dart' show GlassBar, GlassBarBorder;

import 'package:flutter/material.dart';

export 'package:ai_companion/core/mvvm/base_view_model.dart';

/// AppCard = AuroraCard 的设计系统命名别名（同一实现，零双轨）。
typedef AppCard = AuroraCard;

/// AppSheet = FloatingSheet 的设计系统命名别名（同一实现，零双轨）。
typedef AppSheet = FloatingSheet;

/// 玻璃页骨架（F7 新件）：Aurora 真玻璃顶栏页的统一 Scaffold——
/// extendBodyBehindAppBar + 透明 AppBar + flexibleSpace 挂 GlassBar 毛玻璃。
/// chat_screen 顶栏即此范式；新页面顶栏直接用它，不要再手拼 Scaffold+GlassBar。
class GlassScaffold extends StatelessWidget {
  final String? title;
  final Widget? titleWidget;
  final List<Widget>? actions;
  final Widget body;
  final Color? backgroundColor;
  final bool centerTitle;

  const GlassScaffold({
    super.key,
    this.title,
    this.titleWidget,
    this.actions,
    required this.body,
    this.backgroundColor,
    this.centerTitle = false,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      // 真玻璃：body 延伸到 AppBar 后面，flexibleSpace 的模糊才能糊化滚到其下的内容
      extendBodyBehindAppBar: true,
      backgroundColor: backgroundColor,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        centerTitle: centerTitle,
        flexibleSpace: const RepaintBoundary(
          child: GlassBar(
            border: GlassBarBorder.bottom,
            child: SizedBox.expand(),
          ),
        ),
        title: titleWidget ?? (title == null ? null : Text(title!)),
        actions: actions,
      ),
      body: body,
    );
  }
}

/// 状态徽标（F7 新件）：小圆角药丸标签（如「进行中/已失效/权威」等状态标记）。
/// 颜色语义由调用方给定（并随深浅色适配），本件只管形状与排版。
class StateBadge extends StatelessWidget {
  final String label;
  final Color background;
  final Color foreground;
  final IconData? icon;
  final double fontSize;

  const StateBadge({
    super.key,
    required this.label,
    required this.background,
    required this.foreground,
    this.icon,
    this.fontSize = 11,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: fontSize + 3, color: foreground),
            const SizedBox(width: 3),
          ],
          Text(
            label,
            style: TextStyle(
              fontSize: fontSize,
              height: 1.2,
              fontWeight: FontWeight.w600,
              color: foreground,
            ),
          ),
        ],
      ),
    );
  }
}
