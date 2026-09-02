// F7-b（2026-08-31）自 features/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../theme/aurora_tokens.dart';
import '../../theme/tokens.dart';
/// 测量子组件尺寸（真玻璃底栏：把高度回传给消息列表做底部留白）。
class MeasureSize extends StatefulWidget {
  final Widget child;
  final ValueChanged<Size> onChange;
  const MeasureSize({super.key, required this.child, required this.onChange});

  @override
  State<MeasureSize> createState() => _MeasureSizeState();
}

class _MeasureSizeState extends State<MeasureSize> {
  final GlobalKey _key = GlobalKey();
  Size? _last;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _report());
  }

  void _report() {
    final box = _key.currentContext?.findRenderObject() as RenderBox?;
    if (box == null || !box.hasSize) return;
    if (_last != box.size) {
      _last = box.size;
      widget.onChange(box.size);
    }
  }

  @override
  Widget build(BuildContext context) {
    WidgetsBinding.instance.addPostFrameCallback((_) => _report());
    return Container(key: _key, child: widget.child);
  }
}

/// 两条 ISO 时间（UTC）是否在 n 分钟内（北京时区等价，差值不变）
bool withinMinutes(String a, String b, int minutes) {
  DateTime? pa = parseIso(a);
  DateTime? pb = parseIso(b);
  if (pa == null || pb == null) return false;
  return pa.difference(pb).abs().inMinutes < minutes;
}

DateTime? parseIso(String s) {
  try {
    return DateTime.parse(s).toUtc();
  } catch (_) {
    return null;
  }
}

// ── B3 Aurora 私有组件 ──

/// 新消息入场：淡入 + 从下方 8px 上浮 + scale 0.96→1.0（AppMotion.fast）。
/// `reduceMotion` / 系统 disableAnimations 时直显（不建动画）。
/// 仅对新插入项播放（调用方以 animate 控制，复用原 EntranceFade 的判定思路）。
class MessageEntrance extends StatefulWidget {
  final bool animate;
  final Widget child;

  const MessageEntrance({super.key, required this.animate, required this.child});

  @override
  State<MessageEntrance> createState() => _MessageEntranceState();
}

class _MessageEntranceState extends State<MessageEntrance>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl =
      AnimationController(vsync: this, duration: AppMotion.fast);
  late final Animation<double> _opacity =
      CurvedAnimation(parent: _ctrl, curve: AppMotion.emphasized);
  late final Animation<double> _dy =
      Tween<double>(begin: 8, end: 0).animate(_opacity);
  late final Animation<double> _scale =
      Tween<double>(begin: 0.96, end: 1.0).animate(_opacity);

  @override
  void initState() {
    super.initState();
    if (!widget.animate) {
      _ctrl.value = 1.0;
    } else {
      _ctrl.forward();
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // reduceMotion / 系统 disableAnimations：直显（首帧或动画中均可）
    if (MediaQuery.disableAnimationsOf(context) || maybeReduceMotion(context)) {
      _ctrl.stop();
      _ctrl.value = 1.0;
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _opacity,
      child: Transform.translate(
        offset: Offset(0, _dy.value),
        child: Transform.scale(scale: _scale.value, child: widget.child),
      ),
    );
  }
}

/// 按压微弹容器：按下 scale 0.9，松开回弹（AppMotion.fast）；reduceMotion 时不缩放。
class PressScale extends StatefulWidget {
  final bool reduceMotion;
  final Widget child;

  const PressScale({super.key, required this.reduceMotion, required this.child});

  @override
  State<PressScale> createState() => _PressScaleState();
}

class _PressScaleState extends State<PressScale> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: widget.reduceMotion ? null : (_) => setState(() => _pressed = true),
      onTapUp: widget.reduceMotion ? null : (_) => setState(() => _pressed = false),
      onTapCancel: widget.reduceMotion ? null : () => setState(() => _pressed = false),
      child: TweenAnimationBuilder<double>(
        tween: Tween(end: _pressed ? 0.9 : 1.0),
        duration: AppMotion.fast,
        curve: AppMotion.emphasized,
        builder: (context, scale, child) =>
            Transform.scale(scale: scale, child: child),
        child: widget.child,
      ),
    );
  }
}

/// 回底按钮：40px 圆形悬浮，半透明底 + 细描边；有未读新消息时右上角红点。
class BackToBottomButton extends StatelessWidget {
  final bool hasUnread;
  final VoidCallback onTap;

  const BackToBottomButton({super.key, required this.hasUnread, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return GestureDetector(
      key: const Key('backToBottomButton'),
      onTap: onTap,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isDark
                  ? Colors.black.withValues(alpha: 0.45)
                  : Colors.white.withValues(alpha: 0.85),
              border: Border.all(
                color: isDark
                    ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                    : Colors.black.withValues(alpha: AppGlass.borderAlpha),
                width: 0.5,
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.10),
                  blurRadius: 8,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            child: Icon(Icons.arrow_downward, size: 20, color: scheme.onSurfaceVariant),
          ),
          if (hasUnread)
            Positioned(
              right: -1,
              top: -1,
              child: Container(
                width: 10,
                height: 10,
                decoration: const BoxDecoration(
                  color: AppColors.error,
                  shape: BoxShape.circle,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// 读取全局「降低模糊」开关；无 Provider 时按不降级（false）兜底。
bool maybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}

/// 读取全局「降低动效」开关；无 Provider 时按不降级（false）兜底。
bool maybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}
