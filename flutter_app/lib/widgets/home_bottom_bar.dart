import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/aurora_tokens.dart';

/// Aurora 首页底部悬浮胶囊导航栏（Phase 2 B1）。
///
/// 与 [GlassBar] 的关系：模糊/tint/描边走同一套 [AppGlass] 令牌，但底栏是
/// 「悬浮胶囊」形态（四周留边 8、圆角 24、全周描边、选中圆点指示器、按压缩放），
/// GlassBar 的全宽条 + 底边描边无法覆盖，故独立实现。
///
/// 约束：
/// - sigma 经 [AppGlass.effectiveBlur]（全局 reduceBlur 生效）。
/// - reduceMotion（或系统 disableAnimations）时不做按压缩放动画。
/// - 本组件只负责渲染与选中回调；Tab 业务逻辑（PageView 联动等）由调用方处理。

/// 底栏单项：图标（可含徽标）+ 文案。
class HomeBottomBarItem {
  const HomeBottomBarItem({required this.icon, required this.label});

  /// 图标（未选中态；选中态同图标仅变色，与原 NavigationBar 行为一致）。
  final Widget icon;

  final String label;
}

class HomeBottomBar extends StatelessWidget {
  const HomeBottomBar({
    super.key,
    required this.items,
    required this.selectedIndex,
    required this.onSelected,
  });

  final List<HomeBottomBarItem> items;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) {
    final reduceBlur = maybeReduceBlur(context);
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final scheme = Theme.of(context).colorScheme;
    // #12 只有毛玻璃皮肤做半透明+模糊；其余皮肤不透明、不 BackdropFilter
    final glass = AppGlass.isGlassSkin(context);

    final sigma = AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: reduceBlur);
    final background = glass
        ? (isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55))
        : scheme.surface;
    final borderColor = isDark
        ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
        : Colors.black.withValues(alpha: AppGlass.borderAlpha);

    final bar = Container(
      height: 64,
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: borderColor, width: 0.5),
      ),
              child: Row(
                children: [
                  for (var i = 0; i < items.length; i++)
                    _HomeBottomItemButton(
                      item: items[i],
                      selected: i == selectedIndex,
                      onTap: () => onSelected(i),
                    ),
                ],
              ),
            );

    final rounded = ClipRRect(
      borderRadius: BorderRadius.circular(24),
      child: glass
          ? BackdropFilter(
              filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
              child: bar,
            )
          : bar,
    );

    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
        child: rounded,
      ),
    );
  }
}

class _HomeBottomItemButton extends StatefulWidget {
  const _HomeBottomItemButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final HomeBottomBarItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  State<_HomeBottomItemButton> createState() => _HomeBottomItemButtonState();
}

class _HomeBottomItemButtonState extends State<_HomeBottomItemButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final reduceMotion = MediaQuery.disableAnimationsOf(context) || maybeReduceMotion(context);
    final fg = widget.selected ? scheme.primary : scheme.onSurfaceVariant;

    return Expanded(
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        onTapDown: reduceMotion ? null : (_) => setState(() => _pressed = true),
        onTapUp: reduceMotion ? null : (_) => setState(() => _pressed = false),
        onTapCancel: reduceMotion ? null : () => setState(() => _pressed = false),
        child: TweenAnimationBuilder<double>(
          tween: Tween(end: _pressed ? 0.9 : 1.0),
          duration: AppMotion.fast,
          curve: AppMotion.emphasized,
          builder: (context, scale, child) =>
              Transform.scale(scale: scale, child: child),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              // 选中项：图标上方 4px 主题色圆点指示器
              SizedBox(
                height: 6,
                child: Center(
                  child: AnimatedContainer(
                    duration: AppMotion.fast,
                    curve: AppMotion.emphasized,
                    constraints: BoxConstraints.tightFor(
                      width: widget.selected ? 4.0 : 0.0,
                      height: widget.selected ? 4.0 : 0.0,
                    ),
                    decoration: BoxDecoration(
                        color: scheme.primary, shape: BoxShape.circle),
                  ),
                ),
              ),
              IconTheme(
                data: IconThemeData(color: fg),
                child: widget.item.icon,
              ),
              const SizedBox(height: 2),
              Text(
                widget.item.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 11,
                  height: 1,
                  fontWeight: widget.selected ? FontWeight.w600 : FontWeight.w400,
                  color: fg,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 红点徽标 + 呼吸扩散圈（持续循环脉冲；位置/大小/计数逻辑由调用方控制）。
///
/// 循环周期取 [AppMotion.float]（持续循环动效的整周期令牌）。
/// reduceMotion（或系统 disableAnimations）时不渲染扩散圈，只保留静态徽标。
class PulsingBadge extends StatefulWidget {
  const PulsingBadge({super.key, required this.child});

  /// 徽标本体（红点/计数气泡）。
  final Widget child;

  @override
  State<PulsingBadge> createState() => _PulsingBadgeState();
}

class _PulsingBadgeState extends State<PulsingBadge> with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl =
      AnimationController(vsync: this, duration: AppMotion.float);
  bool _reduceMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final rm = MediaQuery.disableAnimationsOf(context) || maybeReduceMotion(context);
    if (rm != _reduceMotion) {
      setState(() => _reduceMotion = rm);
      if (rm) {
        _ctrl.stop();
      } else {
        _ctrl.repeat();
      }
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      clipBehavior: Clip.none,
      children: [
        if (!_reduceMotion)
          Positioned.fill(
            key: const Key('pulsingRing'),
            child: AnimatedBuilder(
              animation: _ctrl,
              builder: (context, _) {
                final t = _ctrl.value;
                return Transform.scale(
                  scale: 1 + 1.4 * t,
                  child: Opacity(
                    opacity: 0.35 * (1 - t),
                    child: Container(
                      decoration: const BoxDecoration(
                        color: Colors.red,
                        shape: BoxShape.circle,
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        widget.child,
      ],
    );
  }
}

/// 读取全局「降低模糊」开关；未包裹 Provider 的测试环境按不降级（false）兜底。
bool maybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}

/// 读取全局「降低动效」开关；未包裹 Provider 的测试环境按不降级（false）兜底。
bool maybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}
