import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';

/// Aurora 基建令牌 —— 动效 / 毛玻璃 / 渐变（2026-08-28，Phase 1）。
///
/// 与 `tokens.dart` 的 AppColors/AppSpacing/AppRadius/AppShadow/AppTypography 互补：
/// `tokens.dart` 收敛「静态视觉原子」，本文件收敛「动效时长/曲线、模糊强度、渐变」三类
/// 跨页面复用的行为令牌。全部与 l10n 无关。
///
/// 约束：
/// - 组件内所有动画时长/曲线只从 [AppMotion] 取，禁止散落硬编码毫秒/Curve。
/// - 组件内所有模糊只经 [AppGlass.effectiveBlur] 取，禁止散落硬编码 sigma。
/// - [AppGradient] 若依赖主题色，必须以静态方法接收 Color 入参，不得塞全局常量
///   导致某一主题色固化失效。

/// 动效令牌：时长（毫秒）+ 常见曲线。
class AppMotion {
  AppMotion._();

  /// 按压 / 微交互（150ms 级）。
  static const Duration fast = Duration(milliseconds: 200);

  /// 通用转场 / 卡片过渡。
  static const Duration normal = Duration(milliseconds: 350);

  /// 大区域 / 慢速过渡。
  static const Duration slow = Duration(milliseconds: 600);

  /// 持续循环动效（浮动/脉冲）的整周期时长。
  static const Duration float = Duration(milliseconds: 3200);

  /// 弹性曲线（卡片弹入 / 高亮入场）。
  static const Curve spring = Curves.easeOutBack;

  /// 强调曲线（展开收起 / 面板过渡，先快后慢）。
  static const Curve emphasized = Curves.easeOutCubic;
}

/// 毛玻璃令牌：模糊强度 + 半透明 tint + 描边透明度 + 全局「降低模糊」换算。
class AppGlass {
  AppGlass._();

  /// 「极光毛玻璃」皮肤 id——全 App 只有该皮肤启用高斯模糊/半透明玻璃，
  /// 其余皮肤（ios/warm/material/paper/neon）一律不做 BackdropFilter。
  static const String glassSkinId = 'glass';

  /// 当前是否处于毛玻璃皮肤。未包裹 Provider（如部分 widget 测试）时返回 false。
  static bool isGlassSkin(BuildContext context) {
    final p = Provider.of<SettingsProvider?>(context, listen: false);
    return p?.skinId == glassSkinId;
  }

  /// 轻模糊（次要浮层）。
  static const double blurLight = 12.0;

  /// 中模糊（卡片 / 条默认）。
  static const double blurMedium = 20.0;

  /// 重模糊（底部面板 / 全屏遮罩）。
  static const double blurHeavy = 32.0;

  /// 浅色 tint 基础透明度。
  static const double tintLight = 0.08;

  /// 深色 tint 基础透明度。
  static const double tintDark = 0.12;

  /// 毛玻璃描边透明度。
  static const double borderAlpha = 0.15;

  /// 全局「降低模糊」开关生效时 sigma 减半（最低 4）。
  ///
  /// `reduceBlur == false` 时原样返回；为 true 时 sigma/2，且不低于 [minBlur]（4）。
  static double effectiveBlur(double sigma, {required bool reduceBlur}) {
    if (!reduceBlur) return sigma;
    final half = sigma / 2;
    return half.clamp(4.0, sigma).toDouble();
  }
}

/// 渐变令牌：极光 / 暖 / 冷 / 遮罩。
///
/// 说明：`aurora` 依赖主题强调色（primary/secondary + surface），故以静态方法接收
/// [Color] 入参返回 `List<Color>`，避免全局常量绑定单一主题色导致换肤后渐变失效；
/// `warm` / `cool` / `overlay` 与主题无关，可直接常量。
class AppGradient {
  AppGradient._();

  /// 极光渐变：三层递进，主题色低透明度 + 表面底色。
  ///
  /// 依赖主题色 → 使用方从 Theme 取 primary/secondary 与 surface 传入。
  static List<Color> aurora({
    required Color primary,
    required Color secondary,
    required Color surface,
  }) {
    return [
      primary.withValues(alpha: 0.12),
      secondary.withValues(alpha: 0.08),
      surface,
    ];
  }

  /// 暖调渐变（v1 方案：暖色 0.15 透明度 + 表面底色）。
  static List<Color> warm({required Color surface}) => [
        const Color(0xFFFFAFA3).withValues(alpha: 0.15),
        surface,
      ];

  /// 冷调渐变（v1 方案：冷色 0.12 透明度 + 表面底色）。
  static List<Color> cool({required Color surface}) => [
        const Color(0xFF82B1FF).withValues(alpha: 0.12),
        surface,
      ];

  /// 遮罩渐变（v1 方案：黑色 0.45 → 0.15 → 透明，用于顶部/底部压暗渐隐）。
  static const List<Color> overlay = [
    Color(0x73000000),
    Color(0x26000000),
    Color(0x00000000),
  ];
}
