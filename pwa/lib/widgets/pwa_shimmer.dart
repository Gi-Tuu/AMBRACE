import 'package:flutter/material.dart';

import '../theme/pwa_motion.dart';

/// 流光骨架容器（PWA 对齐 App 的 `Shimmer`）。
///
/// 只负责「一层横向流光扫过」：把 [PwaSkeletonBox] / [PwaSkeletonCircle] /
/// [PwaSkeletonLine] 包在里面即可。系统「减少动态效果」开启时退化为静态底色，
/// 且不注册 Ticker（同 `PwaBackdrop` 口径）。
class PwaShimmer extends StatefulWidget {
  const PwaShimmer({
    super.key,
    required this.child,
    this.baseColor,
    this.highlightColor,
  });

  final Widget child;
  final Color? baseColor;
  final Color? highlightColor;

  /// 流光一整轮的时长（App 侧同为 1400ms）。
  static const Duration period = PwaMotion.shimmer;

  @override
  State<PwaShimmer> createState() => _PwaShimmerState();
}

class _PwaShimmerState extends State<PwaShimmer>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller =
      AnimationController(vsync: this, duration: PwaShimmer.period);

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MediaQuery.of(context).disableAnimations) {
      if (_controller.isAnimating) _controller.stop();
      _controller.value = 0;
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final base = widget.baseColor ?? scheme.onSurface.withValues(alpha: 0.08);
    final highlight =
        widget.highlightColor ?? scheme.onSurface.withValues(alpha: 0.16);
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        final v = _controller.value;
        return ShaderMask(
          blendMode: BlendMode.srcATop,
          shaderCallback: (rect) => LinearGradient(
            begin: Alignment(-1.0 + 3.0 * v, 0),
            end: Alignment(1.0 + 3.0 * v, 0),
            colors: [base, highlight, base],
            stops: const [0.35, 0.5, 0.65],
          ).createShader(rect),
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

/// 骨架图元：圆角块。
class PwaSkeletonBox extends StatelessWidget {
  const PwaSkeletonBox({
    super.key,
    this.width,
    this.height = 12,
    this.radius = 6,
  });

  final double? width;
  final double height;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

/// 骨架图元：文字条。
class PwaSkeletonLine extends StatelessWidget {
  const PwaSkeletonLine({super.key, this.widthFactor = 1.0, this.height = 12});

  final double widthFactor;
  final double height;

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      alignment: Alignment.centerLeft,
      widthFactor: widthFactor,
      child: PwaSkeletonBox(height: height),
    );
  }
}

/// 骨架图元：圆形（头像位）。
class PwaSkeletonCircle extends StatelessWidget {
  const PwaSkeletonCircle({super.key, this.size = 40});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.08),
        shape: BoxShape.circle,
      ),
    );
  }
}

/// 按页形组合好的骨架屏（`ReaderAsyncView` 默认用列表形态）。
class ReaderSkeletons {
  ReaderSkeletons._();

  /// 列表页：头像＋两行文字 × [rows]。
  static Widget list({int rows = 4}) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        PwaShimmer(
          child: Column(
            children: [
              for (var i = 0; i < rows; i++)
                Padding(
                  padding: const EdgeInsets.only(bottom: 18),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: const [
                      PwaSkeletonCircle(size: 36),
                      SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            PwaSkeletonLine(widthFactor: 0.55),
                            SizedBox(height: 8),
                            PwaSkeletonLine(widthFactor: 0.85, height: 10),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }

  /// 详情页：标题＋若干段文字。
  static Widget detail() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        PwaShimmer(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: const [
              PwaSkeletonLine(widthFactor: 0.45, height: 18),
              SizedBox(height: 16),
              PwaSkeletonLine(widthFactor: 0.9, height: 10),
              SizedBox(height: 8),
              PwaSkeletonLine(widthFactor: 0.8, height: 10),
              SizedBox(height: 8),
              PwaSkeletonLine(widthFactor: 0.6, height: 10),
              SizedBox(height: 20),
              PwaSkeletonBox(height: 120, radius: 10),
            ],
          ),
        ),
      ],
    );
  }

  /// 宫格页（相册/桌面位）。
  static Widget grid({int rows = 2, int columns = 3}) {
    return GridView.count(
      padding: const EdgeInsets.all(12),
      crossAxisCount: columns,
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      childAspectRatio: 1.1,
      children: [
        for (var i = 0; i < rows * columns; i++)
          const PwaShimmer(child: PwaSkeletonBox(height: double.infinity, radius: 10)),
      ],
    );
  }
}
