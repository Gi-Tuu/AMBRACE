import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/aurora_tokens.dart';

/// 聊天时间分隔胶囊（Phase 2 B3）：居中半透明小标签，圆角 10。
///
/// 时间文案由调用方用现有时间工具格式化后传入（组件不内嵌文案）。
/// 玻璃风格用「半透明 + 圆角」实现，不加 BackdropFilter（同屏模糊配额留给顶栏/输入栏）。
class ChatTimeSeparator extends StatelessWidget {
  final String time;

  const ChatTimeSeparator({super.key, required this.time});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Container(
        key: const Key('chatTimeSeparator'),
        margin: const EdgeInsets.symmetric(vertical: 6),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
        decoration: BoxDecoration(
          color: scheme.onSurface.withValues(alpha: 0.06),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(
          time,
          style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
        ),
      ),
    );
  }
}

/// 「正在输入…」三点跳动指示器（Phase 2 B3）。
///
/// - 三个点按 1/3 周期错峰浮动，周期取 [AppMotion.float]；
/// - `reduceMotion`（或系统 disableAnimations）时渲染静态三点，无动画。
/// 未包裹 Provider 的测试环境按不降级（false）兜底。
class TypingIndicator extends StatefulWidget {
  const TypingIndicator({super.key});

  @override
  State<TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<TypingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl =
      AnimationController(vsync: this, duration: AppMotion.float);
  bool _reduceMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final rm = MediaQuery.disableAnimationsOf(context) || _maybeReduceMotion(context);
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
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    if (_reduceMotion) {
      return Row(
        key: const Key('typingDotsStatic'),
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < 3; i++)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 1),
              child: _dot(color, 1),
            ),
        ],
      );
    }
    return Row(
      key: const Key('typingDots'),
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < 3; i++)
          AnimatedBuilder(
            animation: _ctrl,
            builder: (context, _) {
              // 三点各占 1/3 周期，正弦式浮动（上下 2px + 透明度）
              final phase = (_ctrl.value - i / 3) % 1.0;
              final wave = (phase * 2 * 3.14159265);
              final lift = wave >= 0 && wave <= 3.14159265
                  ? (1 - (wave / 3.14159265 - 1).abs()) // 半周期内拱起
                  : 0.0;
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 1),
                child: Transform.translate(
                  offset: Offset(0, -2.5 * lift),
                  child: Opacity(opacity: 0.35 + 0.65 * lift, child: _dot(color, 1)),
                ),
              );
            },
          ),
      ],
    );
  }

  Widget _dot(Color color, double opacity) => Container(
        width: 4,
        height: 4,
        decoration: BoxDecoration(
          color: color.withValues(alpha: opacity),
          shape: BoxShape.circle,
        ),
      );
}

bool _maybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}
