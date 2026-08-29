import 'dart:math' as math;
import 'dart:ui';
import 'package:flutter/material.dart';
import '../../theme/tokens.dart';

/// 头像长按后弹出的气泡卡片数据
class BubbleCardData {
  final IconData icon;
  final String title;
  final String preview;
  final WidgetBuilder detailBuilder;

  const BubbleCardData({
    required this.icon,
    required this.title,
    required this.preview,
    required this.detailBuilder,
  });
}

/// 头像长按气泡浮层：遮罩 + 头像放大 + 4 个卡片气泡伸出。
///
/// - 遮罩：顶部纯色 → 中间半透明 → 底部透明渐变
/// - 头像放大下移（圆心不超过半屏）
/// - 4 个气泡围绕头像伸出，点击放大为固定大小可滚动窗口
/// - 点击遮罩/返回 关闭
class CharacterBubbleOverlay extends StatefulWidget {
  final Widget avatar;
  final List<BubbleCardData> cards;
  final Color barrierColor;

  const CharacterBubbleOverlay({
    super.key,
    required this.avatar,
    required this.cards,
    required this.barrierColor,
  });

  /// 便捷打开方法
  static Future<void> show(
    BuildContext context, {
    required Widget avatar,
    required List<BubbleCardData> cards,
  }) {
    return Navigator.of(context).push(
      PageRouteBuilder(
        opaque: false,
        barrierDismissible: true,
        transitionDuration: const Duration(milliseconds: 320),
        reverseTransitionDuration: const Duration(milliseconds: 260),
        pageBuilder: (_, __, ___) => CharacterBubbleOverlay(
          avatar: avatar,
          cards: cards,
          barrierColor: Theme.of(context).scaffoldBackgroundColor,
        ),
        transitionsBuilder: (_, anim, __, child) {
          return FadeTransition(opacity: anim, child: child);
        },
      ),
    );
  }

  @override
  State<CharacterBubbleOverlay> createState() => _CharacterBubbleOverlayState();
}

class _CharacterBubbleOverlayState extends State<CharacterBubbleOverlay>
    with TickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _scale;
  late final Animation<double> _fade;
  int? _expandedIndex;

  // 4 个气泡围绕头像的角度（左上、右上、左下、右下），弧度
  static const List<double> _angles = [
    -2.356, // 左上 135°
    -0.785, // 右上 45°
    2.356,  // 左下 225°
    0.785,  // 右下 315°
  ];

  static const double _bubbleRadius = 130;
  static const double _avatarNormalRadius = 48;
  static const double _avatarExpandedRadius = 72;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 420),
      reverseDuration: const Duration(milliseconds: 280),
    );
    _scale = CurvedAnimation(parent: _controller, curve: Curves.easeOutBack);
    _fade = CurvedAnimation(parent: _controller, curve: Curves.easeOut);
    _controller.forward();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onCardTap(int index) {
    if (_expandedIndex != null) return;
    setState(() => _expandedIndex = index);
  }

  void _closeExpanded() {
    setState(() => _expandedIndex = null);
  }

  Future<void> _closeAll() async {
    await _controller.reverse();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          // 遮罩：顶部纯色 → 中间半透明 → 底部透明渐变
          Positioned.fill(
            child: GestureDetector(
              onTap: _expandedIndex == null ? _closeAll : _closeExpanded,
              child: AnimatedBuilder(
                animation: _fade,
                builder: (context, _) {
                  return CustomPaint(
                    painter: _BarrierPainter(
                      color: widget.barrierColor,
                      opacity: _fade.value,
                    ),
                    size: Size.infinite,
                  );
                },
              ),
            ),
          ),

          // 头像 + 气泡区域
          Positioned.fill(
            child: SafeArea(
              child: AnimatedBuilder(
                animation: _scale,
                builder: (context, _) {
                  final t = _scale.value;
                  final avatarRadius = lerpDouble(
                    _avatarNormalRadius,
                    _avatarExpandedRadius,
                    t,
                  )!;
                  // 头像中心位置：从顶部偏上 → 屏幕 1/3 处
                  final avatarDy = lerpDouble(
                    size.height * 0.18,
                    size.height * 0.30,
                    t,
                  )!;

                  return Stack(
                    children: [
                      // 4 个气泡
                      for (var i = 0; i < widget.cards.length && i < 4; i++)
                        _buildBubble(i, t, avatarDy, avatarRadius, size, scheme),

                      // 放大的头像
                      Positioned(
                        top: avatarDy - avatarRadius,
                        left: size.width / 2 - avatarRadius,
                        child: Transform.scale(
                          scale: t,
                          child: Container(
                            width: avatarRadius * 2,
                            height: avatarRadius * 2,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              boxShadow: AppShadow.heavy,
                            ),
                            child: ClipOval(child: widget.avatar),
                          ),
                        ),
                      ),
                    ],
                  );
                },
              ),
            ),
          ),

          // 放大的详情窗口
          if (_expandedIndex != null)
            _buildDetailWindow(_expandedIndex!, scheme, size),
        ],
      ),
    );
  }

  Widget _buildBubble(
    int index,
    double t,
    double avatarDy,
    double avatarRadius,
    Size size,
    ColorScheme scheme,
  ) {
    final card = widget.cards[index];
    final angle = _angles[index];

    // 气泡位置：从头像中心向外伸出
    final startX = size.width / 2;
    final startY = avatarDy;
    final endX = startX + math.cos(angle) * _bubbleRadius;
    final endY = startY + math.sin(angle) * _bubbleRadius * 0.85;

    // 每个气泡错峰 60ms
    final bubbleT = Curves.easeOutBack.transform(
      ((t - index * 0.08).clamp(0.0, 1.0)),
    );

    final dx = lerpDouble(startX, endX, bubbleT)!;
    final dy = lerpDouble(startY, endY, bubbleT)!;

    const bubbleWidth = 150.0;
    const bubbleHeight = 90.0;

    return Positioned(
      left: dx - bubbleWidth / 2,
      top: dy - bubbleHeight / 2,
      child: Opacity(
        opacity: bubbleT,
        child: Transform.scale(
          scale: 0.6 + 0.4 * bubbleT,
          child: GestureDetector(
            onTap: () => _onCardTap(index),
            child: Container(
              width: bubbleWidth,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: scheme.surface.withValues(alpha: 0.92),
                borderRadius: BorderRadius.circular(AppRadius.lg),
                boxShadow: AppShadow.medium,
                border: Border.all(
                  color: scheme.outlineVariant.withValues(alpha: 0.3),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Icon(card.icon, size: 16, color: scheme.primary),
                      const SizedBox(width: 6),
                      Text(
                        card.title,
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                          color: scheme.onSurface,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    card.preview,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 11,
                      height: 1.35,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildDetailWindow(int index, ColorScheme scheme, Size size) {
    final card = widget.cards[index];
    final maxWidth = math.min(size.width - 40, 380.0);
    final maxHeight = size.height * 0.55;

    return Positioned.fill(
      child: GestureDetector(
        onTap: _closeExpanded,
        child: Container(
          color: Colors.black.withValues(alpha: 0.35),
          child: Center(
            child: GestureDetector(
              onTap: () {}, // 阻止冒泡
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0.85, end: 1.0),
                duration: const Duration(milliseconds: 220),
                curve: Curves.easeOutBack,
                builder: (context, scale, child) {
                  return Transform.scale(scale: scale, child: child);
                },
                child: Container(
                  width: maxWidth,
                  constraints: BoxConstraints(maxHeight: maxHeight),
                  decoration: BoxDecoration(
                    color: scheme.surface,
                    borderRadius: BorderRadius.circular(AppRadius.xl),
                    boxShadow: AppShadow.heavy,
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // 标题栏
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 14, 8, 10),
                        child: Row(
                          children: [
                            Icon(card.icon, size: 20, color: scheme.primary),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                card.title,
                                style: TextStyle(
                                  fontSize: AppTypography.titleSize,
                                  fontWeight: AppTypography.titleWeight,
                                  color: scheme.onSurface,
                                ),
                              ),
                            ),
                            IconButton(
                              icon: const Icon(Icons.close, size: 20),
                              onPressed: _closeExpanded,
                              visualDensity: VisualDensity.compact,
                            ),
                          ],
                        ),
                      ),
                      const Divider(height: 1),
                      // 内容区（可滚动）
                      Flexible(
                        child: SingleChildScrollView(
                          padding: const EdgeInsets.all(16),
                          child: card.detailBuilder(context),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 遮罩画笔：顶部纯色 → 中间半透明 → 底部透明渐变
class _BarrierPainter extends CustomPainter {
  final Color color;
  final double opacity;

  _BarrierPainter({required this.color, required this.opacity});

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          color.withValues(alpha: 0.95 * opacity),
          color.withValues(alpha: 0.6 * opacity),
          color.withValues(alpha: 0.0),
        ],
        stops: const [0.0, 0.45, 0.75],
      ).createShader(rect);
    canvas.drawRect(rect, paint);
  }

  @override
  bool shouldRepaint(covariant _BarrierPainter oldDelegate) =>
      oldDelegate.opacity != opacity;
}
