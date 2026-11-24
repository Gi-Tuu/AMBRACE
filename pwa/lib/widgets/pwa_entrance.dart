import 'package:flutter/material.dart';

import '../theme/pwa_motion.dart';

/// 通用入场动画（PWA 对齐 App 的 `EntranceFade`）：淡入＋轻微上浮。
///
/// - 列表里新插入的项在挂载时播放一次；已有项因 State 复用不会重播。
/// - [animate] 传 false 时不播（用于首帧装载历史，避免整屏弹一遍）。
/// - 系统「减少动态效果」开启时直接落到终态。
class PwaEntrance extends StatefulWidget {
  const PwaEntrance({
    super.key,
    required this.child,
    this.animate = true,
    this.duration = PwaMotion.enter,
    this.curve = Curves.easeOut,
    this.offset = const Offset(0, 8),
  });

  final Widget child;
  final bool animate;
  final Duration duration;
  final Curve curve;
  final Offset offset;

  @override
  State<PwaEntrance> createState() => _PwaEntranceState();
}

class _PwaEntranceState extends State<PwaEntrance>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller =
      AnimationController(vsync: this, duration: widget.duration);
  late final CurvedAnimation _curved =
      CurvedAnimation(parent: _controller, curve: widget.curve);
  late final Animation<double> _opacity = _curved;
  late final Animation<Offset> _slide =
      Tween<Offset>(begin: widget.offset, end: Offset.zero).animate(_curved);

  bool _decided = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_decided) return;
    _decided = true;
    final reduce = MediaQuery.of(context).disableAnimations;
    if (!widget.animate || reduce) {
      _controller.value = 1;
    } else {
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _curved.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.animate || _controller.isCompleted) return widget.child;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) => Opacity(
        opacity: _opacity.value.clamp(0.0, 1.0),
        child: Transform.translate(
          offset: _slide.value,
          child: child,
        ),
      ),
      child: widget.child,
    );
  }
}

/// 列表入场控制器：首帧 [PwaListEntrance.builder] 收到 `animate=false`（历史不弹），
/// 首帧之后再收到 `true`（后续插入的新项播放入场）。
///
/// 用法：
/// ```dart
/// PwaListEntrance(builder: (animate) => ListView(children: [
///   for (final it in items)
///     PwaEntrance(key: ValueKey(it.id), animate: animate, child: ...),
/// ]))
/// ```
class PwaListEntrance extends StatefulWidget {
  const PwaListEntrance({super.key, required this.builder});

  final Widget Function(bool animate) builder;

  @override
  State<PwaListEntrance> createState() => _PwaListEntranceState();
}

class _PwaListEntranceState extends State<PwaListEntrance> {
  bool _settled = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() => _settled = true);
    });
  }

  @override
  Widget build(BuildContext context) => widget.builder(_settled);
}
