import "package:flutter/material.dart";

/// AMBRACE 设计令牌（Design Token）原子 —— 全局唯一来源（2026-08-23 第三阶段）。
///
/// 在 AppTheme 体系内补齐，运行时不需要加载 tokens.yaml。
/// 五个维度：AppColors / AppSpacing / AppRadius / AppShadow / AppTypography。
///
/// 约束：
/// - 色值保持与既有 AppTheme 视觉默认**完全一致**，收敛硬编码色值但不改变任何页面视觉。
/// - 全部字段为 `static const`，可直接在 const 上下文中引用（如 `const TextStyle(color: AppColors.textSecondary)`）。
/// - 采用顶层 class 组织命名空间（Dart 不允许 class 嵌套 class）。

/// 颜色语义原子（浅色主题主用值；深色主题对应值见 AppTheme）。
/// 背景 / 卡片 / 分隔线 / 文字主次 / 边框 / 语义色统一在此，取代散落的硬编码 hex。
class AppColors {
  AppColors._();

  // ---- 背景 ----
  static const Color bgLight = Color(0xFFF2F2F7);
  static const Color bgDark = Color(0xFF0E0E10);

  // ---- 卡片 ----
  static const Color cardLight = Color(0xFFFFFFFF);
  static const Color cardDark = Color(0xFF1C1C1E);
  static const Color surfaceAlt = Color(0xFFE9E9EB);

  // ---- 分隔线 / 边界 ----
  static const Color dividerLight = Color(0xFFECECEF);
  static const Color dividerDark = Color(0xFF2C2C2E);
  static const Color separator = Color(0xFFC6C6C8);
  static const Color hairline = Color(0xFFD1D1D6);
  static const Color border = Color(0xFFD1D1D6);

  // ---- 文字（主 / 次 / 弱 / 三级）----
  static const Color textPrimary = Color(0xFF1C1C1E);
  static const Color textSecondary = Color(0xFF8E8E93);
  static const Color textMuted = Color(0xFF6E6E73);
  static const Color textTertiary = Color(0xFFC7C7CC);

  // ---- 品牌 / 语义 ----
  static const Color accent = Color(0xFF007AFF);
  static const Color success = Color(0xFF34C759);
  static const Color warning = Color(0xFFFF9500);
  static const Color error = Color(0xFFFF3B30);
  static const Color white = Color(0xFFFFFFFF);
}

/// 间距原子（4 的倍数阶梯）。
class AppSpacing {
  AppSpacing._();
  static const double xxs = 4;
  static const double xs = 8;
  static const double sm = 12;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
}

/// 圆角原子。
class AppRadius {
  AppRadius._();
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
}

/// 阴影原子（轻 / 中 / 重三档；均为无方向柔和投影，配合 iOS 无阴影 AppBar/NavBar 规范）。
class AppShadow {
  AppShadow._();
  static const List<BoxShadow> light = [
    BoxShadow(color: Color(0x14000000), blurRadius: 8, offset: Offset(0, 2)),
  ];
  static const List<BoxShadow> medium = [
    BoxShadow(color: Color(0x1A000000), blurRadius: 16, offset: Offset(0, 4)),
  ];
  static const List<BoxShadow> heavy = [
    BoxShadow(color: Color(0x24000000), blurRadius: 28, offset: Offset(0, 8)),
  ];

  /// 上方投影变体（与 light 同强度、offset 反向）：供输入栏 / 底部上浮工具栏复用，
  /// 保持「无阴影 AppBar/NavBar + 柔和投影」规范的一致性。
  static const List<BoxShadow> top = [
    BoxShadow(color: Color(0x14000000), blurRadius: 8, offset: Offset(0, -2)),
  ];
}

/// 排版原子：标题 / 正文 / 辅助 的字号、字重、行高常量。
/// 只作为常量来源，不在本文中强制覆盖全局 TextTheme（避免改变既有视觉默认）。
class AppTypography {
  AppTypography._();
  // 标题
  static const double titleSize = 17;
  static const FontWeight titleWeight = FontWeight.w600;
  // 正文
  static const double bodySize = 15;
  static const double bodyLineHeight = 1.35;
  // 辅助
  static const double helperSize = 13;
  static const double captionSize = 11;
  static const double captionHeight = 1.2;
}
