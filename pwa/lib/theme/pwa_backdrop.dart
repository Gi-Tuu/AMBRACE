import 'package:flutter/material.dart';

import 'pwa_motion.dart';

/// 皮肤装饰动效层（PWA 对齐 App 的皮肤级装饰；不含各功能页的专属动画）。
///
/// - 底色渐变按皮肤取（aurora / warm / cool / 纸感 / 古典），换肤时 `AnimatedContainer` 平滑过渡；
/// - 两层低透明度主题色光晕缓慢漂流（周期 [PwaMotion.drift]），营造 App 的「极光呼吸」观感；
/// - **paper / aegean 保持静止**（纸感与古典皮肤在 App 侧也不做动效）；
/// - 系统「减少动态效果」开启时一律停在静止帧，且不注册 Ticker（省电、可被测试断言）。
class PwaBackdrop extends StatefulWidget {
  const PwaBackdrop({super.key, required this.skinId, required this.child});

  /// 当前皮肤 id（与 App `skin_registry` 对齐：ios/warm/material/paper/neon/glass/aegean）。
  final String skinId;

  final Widget child;

  /// 带持续动效的皮肤；其余皮肤只做静态装饰渐变。
  static const Set<String> movingSkins = {'ios', 'warm', 'material', 'neon', 'glass'};

  @override
  State<PwaBackdrop> createState() => PwaBackdropState();
}

class PwaBackdropState extends State<PwaBackdrop>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: PwaMotion.drift,
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncMotion();
  }

  @override
  void didUpdateWidget(PwaBackdrop oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.skinId != widget.skinId) _syncMotion();
  }

  void _syncMotion() {
    final reduce = MediaQuery.of(context).disableAnimations;
    final shouldAnimate = !reduce && PwaBackdrop.movingSkins.contains(widget.skinId);
    if (shouldAnimate) {
      if (!_controller.isAnimating) _controller.repeat(reverse: true);
    } else {
      if (_controller.isAnimating) _controller.stop();
      _controller.value = 0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// 测试用：当前是否在持续播放装饰动画。
  bool get isTicking => _controller.isAnimating;

  List<Color> _baseColors(ColorScheme scheme) {
    final dark = scheme.brightness == Brightness.dark;
    switch (widget.skinId) {
      case 'warm':
        return [
          const Color(0xFFFFAFA3).withValues(alpha: dark ? 0.10 : 0.16),
          scheme.surface,
        ];
      case 'material':
        return [scheme.primary.withValues(alpha: dark ? 0.10 : 0.07), scheme.surface];
      case 'paper':
        return [
          const Color(0xFFE7DCC7).withValues(alpha: dark ? 0.05 : 0.12),
          scheme.surface,
        ];
      case 'neon':
        return [
          scheme.primary.withValues(alpha: dark ? 0.16 : 0.08),
          scheme.secondary.withValues(alpha: dark ? 0.07 : 0.04),
          scheme.surface,
        ];
      case 'glass':
        return [
          scheme.primary.withValues(alpha: 0.12),
          scheme.secondary.withValues(alpha: 0.08),
          scheme.surface,
        ];
      case 'aegean':
        return [
          const Color(0xFF2E5E8A).withValues(alpha: dark ? 0.14 : 0.10),
          scheme.surface,
        ];
      default: // ios
        return [
          scheme.primary.withValues(alpha: dark ? 0.12 : 0.09),
          scheme.secondary.withValues(alpha: dark ? 0.07 : 0.05),
          scheme.surface,
        ];
    }
  }

  Widget _glow(Color color, double alpha, Alignment center) {
    return Positioned.fill(
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: center,
            radius: 0.95,
            colors: [
              color.withValues(alpha: alpha),
              color.withValues(alpha: 0),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Stack(
      fit: StackFit.expand,
      children: [
        AnimatedContainer(
          duration: PwaMotion.normal,
          curve: PwaMotion.emphasized,
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: _baseColors(scheme),
            ),
          ),
        ),
        AnimatedBuilder(
          animation: _controller,
          builder: (context, _) {
            final t = Curves.easeInOut.transform(_controller.value);
            return IgnorePointer(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  _glow(
                    scheme.primary,
                    0.05 + 0.05 * t,
                    Alignment(-0.85 + 0.20 * t, -0.90),
                  ),
                  _glow(
                    scheme.secondary,
                    0.05 + 0.04 * (1 - t),
                    Alignment(0.90, 0.75 - 0.25 * t),
                  ),
                ],
              ),
            );
          },
        ),
        widget.child,
      ],
    );
  }
}
