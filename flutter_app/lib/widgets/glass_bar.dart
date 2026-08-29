import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/aurora_tokens.dart';

/// 玻璃描边位置。
enum GlassBarBorder { top, bottom, none }

/// Aurora 毛玻璃条 —— 用于 AppBar / 底栏 / 输入栏 / TabBar 的半透明+模糊条。
///
/// 「真玻璃」前提：调用方必须让可滚动内容从本组件下方穿过（例如
/// Scaffold `extendBodyBehindAppBar: true`，或把本组件放进 Stack 浮在
/// ListView 之上），否则 BackdropFilter 背后没有内容，只会是半透明纯色。
///
/// 关键约束：
/// - `BackdropFilter` sigma 统一经 [AppGlass.effectiveBlur] 取（受全局 reduceBlur 控制）。
/// - 半透明背景：浅色 `Colors.white(0.55)`，深色 `Colors.black(0.30)`；
///   皮肤提供 inputBarBg / 玻璃色时可经 [backgroundColor] 显式覆盖（此时不模糊）。
/// - 玻璃描边色按 [AppGlass.borderAlpha]，方向由 [border] 决定。
///
/// 无 Provider 包裹时按不降级兜底（reduceBlur 视为 false）。
class GlassBar extends StatelessWidget {
  /// 条内容。
  final Widget child;

  /// 高度（为 null 时由内容决定）。
  final double? height;

  /// 描边位置（默认底部，顶栏用 bottom，底栏/输入栏用 top）。
  final GlassBarBorder border;

  /// 条透明度（调用方可传 0.7 做滚动透明度，默认 1.0）。
  final double opacity;

  /// 模糊强度（默认 [AppGlass.blurMedium]）。
  final double blur;

  /// 显式背景色（皮肤 inputBarBg 等）。设置后**不再做 BackdropFilter**，
  /// 直接以该色作为不透明/半透明背景——用于皮肤要固定栏色的场景。
  final Color? backgroundColor;

  /// 内边距（默认无，由调用方自行控制）。
  final EdgeInsetsGeometry? padding;

  const GlassBar({
    super.key,
    required this.child,
    this.height,
    this.border = GlassBarBorder.bottom,
    this.opacity = 1.0,
    this.blur = AppGlass.blurMedium,
    this.backgroundColor,
    this.padding,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    // 皮肤固定色：直接铺色，不做模糊。
    if (backgroundColor != null) {
      return Container(
        height: height,
        padding: padding,
        decoration: BoxDecoration(
          color: backgroundColor,
          border: _border(isDark),
        ),
        child: child,
      );
    }

    // #12 非毛玻璃皮肤：不透明栏、不做 BackdropFilter（原生观感）
    if (!AppGlass.isGlassSkin(context)) {
      return Container(
        height: height,
        padding: padding,
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surface,
          border: _border(isDark),
        ),
        child: child,
      );
    }

    final reduceBlur = _maybeReduceBlur(context);
    final sigma = AppGlass.effectiveBlur(blur, reduceBlur: reduceBlur);

    final background = isDark
        ? Colors.black.withValues(alpha: 0.30 * opacity.clamp(0.0, 1.0))
        : Colors.white.withValues(alpha: 0.55 * opacity.clamp(0.0, 1.0));

    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
        child: Container(
          height: height,
          padding: padding,
          decoration: BoxDecoration(
            color: background,
            border: _border(isDark),
          ),
          child: child,
        ),
      ),
    );
  }

  Border? _border(bool isDark) {
    if (border == GlassBarBorder.none) return null;
    final color = isDark
        ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
        : Colors.black.withValues(alpha: AppGlass.borderAlpha);
    final side = BorderSide(color: color, width: 0.5);
    switch (border) {
      case GlassBarBorder.top:
        return Border(top: side);
      case GlassBarBorder.bottom:
        return Border(bottom: side);
      case GlassBarBorder.none:
        return null;
    }
  }
}

/// 读取全局「降低模糊」开关，未包裹 Provider 的测试环境按不降级（false）兜底。
bool _maybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}
