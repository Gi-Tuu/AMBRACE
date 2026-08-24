import "package:flutter/material.dart";

/// 通用入场动画：淡入 + 轻微上浮（opacity 0→1、translateY 8→0）。
///
/// 用在消息列表新插入项上：
/// - 新建（insert）的 item 会在 initState 时触发一次动画；
/// - 列表因其它原因 setState 重建时，已有 item 复用 State（不重播），
///   因此天然做到「只对新插入项动画，不做全量重绘动画」。
/// - [animate] 传 false 时不播动画（用于列表首帧装载历史消息，避免整屏弹一遍）。
class EntranceFade extends StatefulWidget {
  const EntranceFade({
    super.key,
    required this.child,
    this.animate = true,
    this.duration = const Duration(milliseconds: 280),
    this.curve = Curves.easeOut,
    this.offset = const Offset(0, 8),
  });

  final Widget child;

  /// 是否在挂载时播放；首帧装载历史消息传 false。
  final bool animate;

  /// 动画时长（第三阶段约定：0.25s ~ 0.3s，取 280ms，不拖沓）。
  final Duration duration;

  final Curve curve;

  /// 初始位移（向上上浮的起点，默认 translateY=8）。
  final Offset offset;

  @override
  State<EntranceFade> createState() => _EntranceFadeState();
}

class _EntranceFadeState extends State<EntranceFade>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _opacity;
  late final Animation<Offset> _slide;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: widget.duration);
    final curved = CurvedAnimation(parent: _controller, curve: widget.curve);
    _opacity = Tween<double>(begin: 0, end: 1).animate(curved);
    _slide = Tween<Offset>(begin: widget.offset, end: Offset.zero).animate(curved);
    if (widget.animate) {
      _controller.forward();
    } else {
      _controller.value = 1.0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _opacity,
      child: SlideTransition(position: _slide, child: widget.child),
    );
  }
}
