import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/aurora_tokens.dart';
import '../theme/skins/skin_colors.dart';
import '../theme/tokens.dart';

/// Aurora 卡片 —— IosCardGroup 的升级替代，用于角色卡片 / AI 好友列表等。
///
/// 关键约束：
/// - 圆角固定 20（`AppRadius.lg` 现值为 16，故此处用 20 常量并注释说明）。
/// - 无边框；阴影 `AppShadow.medium`（深色下 `AppShadow.light`）。
/// - 背景：glass 皮肤（`SkinColors.glassBackground != null`）→ 半透明 `glassBackground` +
///   描边 `glassBorder`；非 glass → `scheme.surface`。
/// - `blurred == true` 时包 `BackdropFilter`，sigma 统一经 `AppGlass.effectiveBlur` 取。
/// - `onTap != null` 时按压 `AnimatedScale` 0.98 / `AppMotion.fast`；`reduceMotion` 时不缩放。
/// - 同屏 BackdropFilter ≤3：本组件默认不模糊，由调用方按需开启 `blurred` 并控制数量。
///
/// 无 Provider 包裹时按「不降级」兜底（`reduceMotion`/`reduceBlur` 视为 false），
/// 正常场景使用方应在父级提供 `ChangeNotifierProvider<SettingsProvider>`。
class AuroraCard extends StatelessWidget {
  /// 内容组件。
  final Widget child;

  /// 内容内边距（默认 16）。
  final EdgeInsetsGeometry padding;

  /// 点击回调；非空时启用按压缩放动画。
  final VoidCallback? onTap;

  /// 顶部渐变高光条（4px），默认关闭。
  final bool highlight;

  /// 是否包 `BackdropFilter`（默认 false，受「同屏 ≤3」约束由调用方控制）。
  final bool blurred;

  const AuroraCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.onTap,
    this.highlight = false,
    this.blurred = false,
  });

  @override
  Widget build(BuildContext context) {
    final settings = _maybeSettings(context);
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final skinColors = Theme.of(context).extension<SkinColors>();
    final glassBackground = skinColors?.glassBackground;
    final glassBorder = skinColors?.glassBorder;
    // #12 只有极光毛玻璃皮肤才走半透明玻璃；warm/neon 等即便配了 glassBackground 也不玻璃化
    final isGlass = settings?.skinId == AppGlass.glassSkinId;

    // AppRadius.lg（16）与 AuroraCard 的圆角 20 不同，此处显式用 20 常量。
    const radius = BorderRadius.all(Radius.circular(20));

    final reduceBlur = settings?.reduceBlur ?? false;
    final sigma = AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: reduceBlur);

    Widget card = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: isGlass ? glassBackground : scheme.surface,
        borderRadius: radius,
        border: (isGlass && glassBorder != null)
            ? Border.all(color: glassBorder)
            : null,
        boxShadow: isDark ? AppShadow.light : AppShadow.medium,
      ),
      child: child,
    );

    if (highlight) {
      final gradientColors = AppGradient.aurora(
        primary: scheme.primary,
        secondary: scheme.secondary,
        surface: scheme.surface,
      );
      card = Stack(
        children: [
          card,
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              height: 4,
              decoration: BoxDecoration(
                borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
                gradient: LinearGradient(colors: gradientColors),
              ),
            ),
          ),
        ],
      );
    }

    Widget inner = card;
    // #12 非毛玻璃皮肤不做 BackdropFilter（避免误玻璃化）
    if (blurred && isGlass) {
      inner = ClipRRect(
        borderRadius: radius,
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
          child: card,
        ),
      );
    }

    if (onTap == null) return inner;

    final reduceMotion = settings?.reduceMotion ?? false;
    return _Pressable(
      onTap: onTap!,
      reduceMotion: reduceMotion,
      child: inner,
    );
  }
}

/// 按压缩放容器：`reduceMotion` 时不做缩放动画（保持 1.0），否则按压到 0.98。
class _Pressable extends StatefulWidget {
  final VoidCallback onTap;
  final bool reduceMotion;
  final Widget child;

  const _Pressable({
    required this.onTap,
    required this.reduceMotion,
    required this.child,
  });

  @override
  State<_Pressable> createState() => _PressableState();
}

class _PressableState extends State<_Pressable> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    if (widget.reduceMotion) {
      return GestureDetector(
        onTap: widget.onTap,
        child: widget.child,
      );
    }
    return AnimatedScale(
      scale: _pressed ? 0.98 : 1.0,
      duration: AppMotion.fast,
      curve: AppMotion.emphasized,
      child: GestureDetector(
        onTap: widget.onTap,
        onTapDown: (_) => setState(() => _pressed = true),
        onTapCancel: () => setState(() => _pressed = false),
        onTapUp: (_) => setState(() => _pressed = false),
        child: widget.child,
      ),
    );
  }
}

/// 读取 SettingsProvider，未包裹 Provider 的测试环境按不降级（null）兜底。
SettingsProvider? _maybeSettings(BuildContext context) {
  try {
    return context.watch<SettingsProvider>();
  } catch (_) {
    return null;
  }
}
