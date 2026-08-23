
import 'package:flutter/material.dart';
import '../theme/tokens.dart';

/// 轻量 shimmer 骨架屏流光容器。
///
/// 用 [AnimationController] 驱动一个横向滑动的渐变蒙层，罩在骨架图形
/// （[SkeletonBox] / [SkeletonCircle] / [SkeletonLine]）上，产生「流光扫过」的效果。
/// 纯自绘、不引入第三方依赖，作为主界面四个页面加载态的骨架屏。
///
/// 注意：建议让 [Shimmer] 只包住骨架图形本身（圆头像/文字条/图块），
/// 而把白色卡片容器留在外层，避免 ShaderMask 的 `srcATop` 把整张卡片刷成统一灰色。
class Shimmer extends StatefulWidget {
  const Shimmer({
    super.key,
    required this.child,
    this.baseColor,
    this.highlightColor,
    this.duration = const Duration(milliseconds: 1400),
  });

  final Widget child;
  final Color? baseColor;
  final Color? highlightColor;
  final Duration duration;

  @override
  State<Shimmer> createState() => _ShimmerState();
}

class _ShimmerState extends State<Shimmer> with SingleTickerProviderStateMixin {
  late final AnimationController _controller =
      AnimationController(vsync: this, duration: widget.duration)..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final base = widget.baseColor ?? scheme.surfaceContainerHighest;
    final highlight = widget.highlightColor ?? scheme.surfaceContainerLow;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        // 在 -1..1 间往复，换算成像素平移量驱动渐变滑动
        final dx = (2.0 * _controller.value - 1.0);
        return ShaderMask(
          blendMode: BlendMode.srcATop,
          shaderCallback: (bounds) {
            return LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: [base, highlight, base],
              stops: const [0.25, 0.5, 0.75],
              transform: _SlideGradientTransform(dx * bounds.width),
            ).createShader(bounds);
          },
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

class _SlideGradientTransform extends GradientTransform {
  const _SlideGradientTransform(this.dx);
  final double dx;

  @override
  Matrix4? transform(Rect bounds, {TextDirection? textDirection}) =>
      Matrix4.translationValues(dx, 0, 0);
}

/// 骨架盒子：圆角矩形占位。
class SkeletonBox extends StatelessWidget {
  const SkeletonBox({super.key, this.width, this.height, this.radius = 8, this.borderRadius});

  final double? width;
  final double? height;
  final double radius;
  final BorderRadius? borderRadius;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: borderRadius ?? BorderRadius.circular(radius),
      ),
    );
  }
}

/// 骨架圆：头像/图标占位。
class SkeletonCircle extends StatelessWidget {
  const SkeletonCircle({super.key, required this.size});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        shape: BoxShape.circle,
      ),
    );
  }
}

/// 骨架文字条：整圆角细条。
class SkeletonLine extends StatelessWidget {
  const SkeletonLine({super.key, this.width, this.height = 12});

  final double? width;
  final double height;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(height / 2),
      ),
    );
  }
}

/// AI 好友列表骨架：圆头像 + 两行文字条 + 右侧小箭头，匹配真实卡片结构。
/// Shimmer 只罩住骨架图形，外层 Card 保持纯色卡片底。
class CharacterListSkeleton extends StatelessWidget {
  const CharacterListSkeleton({super.key, this.itemCount = 6});

  final int itemCount;

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(AppSpacing.sm, AppSpacing.xxs, AppSpacing.sm, AppSpacing.sm),
      itemCount: itemCount,
      itemBuilder: (context, index) {
        return Card(
          margin: const EdgeInsets.only(bottom: AppSpacing.xs),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: const Shimmer(
              child: Row(
                children: [
                  SkeletonCircle(size: 44),
                  SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SkeletonLine(width: 120, height: 14),
                        SizedBox(height: 8),
                        SkeletonLine(width: 200, height: 12),
                      ],
                    ),
                  ),
                  SkeletonBox(width: 12, height: 12),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 朋友圈列表骨架：头像 + 名字 + 内容多行 + 图片九宫格，匹配 MomentCard 结构。
class MomentsSkeleton extends StatelessWidget {
  const MomentsSkeleton({super.key, this.itemCount = 4});

  final int itemCount;

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      itemCount: itemCount,
      itemBuilder: (context, index) {
        return Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: const Shimmer(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      SkeletonCircle(size: 40),
                      SizedBox(width: 10),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SkeletonLine(width: 100, height: 13),
                          SizedBox(height: 6),
                          SkeletonLine(width: 60, height: 11),
                        ],
                      ),
                    ],
                  ),
                  SizedBox(height: 12),
                  SkeletonLine(width: double.infinity, height: 12),
                  SizedBox(height: 8),
                  SkeletonLine(width: 220, height: 12),
                  SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(child: SkeletonBox(height: 96, radius: 8)),
                      SizedBox(width: 8),
                      Expanded(child: SkeletonBox(height: 96, radius: 8)),
                      SizedBox(width: 8),
                      Expanded(child: SkeletonBox(height: 96, radius: 8)),
                    ],
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 手机（群聊交互）页面骨架：两列手机应用栅格，匹配 _PhoneTile 布局。
class AiInteractionSkeleton extends StatelessWidget {
  const AiInteractionSkeleton({super.key, this.itemCount = 6});

  final int itemCount;

  @override
  Widget build(BuildContext context) {
    return GridView.builder(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        mainAxisSpacing: 12,
        crossAxisSpacing: 12,
        childAspectRatio: 0.62,
      ),
      itemCount: itemCount,
      itemBuilder: (context, index) {
        return const Shimmer(
          child: Padding(
            padding: EdgeInsets.all(16),
            child: Align(
              alignment: Alignment.topCenter,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SkeletonBox(width: 72, height: 72, radius: 18),
                  SizedBox(height: 14),
                  SkeletonLine(width: 90, height: 12),
                  SizedBox(height: 8),
                  SkeletonLine(width: 60, height: 11),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 小家可视化页面骨架：顶部三条状态进度条 + 房间 tab 行 + 房间主体大卡片。
class HomeVisualSkeleton extends StatelessWidget {
  const HomeVisualSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return const Shimmer(
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: SkeletonBox(height: 10, radius: 5)),
                SizedBox(width: 10),
                Expanded(child: SkeletonBox(height: 10, radius: 5)),
                SizedBox(width: 10),
                Expanded(child: SkeletonBox(height: 10, radius: 5)),
              ],
            ),
            SizedBox(height: 16),
            Row(
              children: [
                SkeletonBox(width: 72, height: 32, radius: 16),
                SizedBox(width: 10),
                SkeletonBox(width: 72, height: 32, radius: 16),
              ],
            ),
            SizedBox(height: 20),
            Expanded(child: SkeletonBox(height: double.infinity, radius: 20)),
          ],
        ),
      ),
    );
  }
}
