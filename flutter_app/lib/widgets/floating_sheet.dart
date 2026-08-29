import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/aurora_tokens.dart';

/// 弹出底部面板（生活 / 发布 / 详情等），封装 `showModalBottomSheet`。
///
/// 视觉：
/// - 圆角顶部 24、`backgroundColor` 透明。
/// - 内容容器 = 毛玻璃（BackdropFilter + `AppGlass.blurHeavy` + 浅 0.72 / 深 0.60 tint）。
/// - 顶部 40×4 拖拽条 + `SafeArea`。
///
/// 交互：半展开 / 全展开两档 —— 点击拖拽条区域在
/// [minHeightFraction]（默认 0.35）与 [maxHeightFraction]（默认 0.85）间切换，
/// 用 `AnimatedContainer`（`AppMotion.normal`）过渡；`expandable == false` 时仅一档。
/// 内容超出时 `SingleChildScrollView`。
class FloatingSheet extends StatefulWidget {
  /// 可选标题。
  final String? title;

  /// 面板内容。
  final Widget child;

  /// 全展开时占屏高度比例。
  final double maxHeightFraction;

  /// 半展开时占屏高度比例。
  final double minHeightFraction;

  /// 是否支持半展开/全展开切换（默认 true）。
  final bool expandable;

  const FloatingSheet({
    super.key,
    this.title,
    required this.child,
    this.maxHeightFraction = 0.85,
    this.minHeightFraction = 0.35,
    this.expandable = true,
  });

  @override
  State<FloatingSheet> createState() => _FloatingSheetState();
}

class _FloatingSheetState extends State<FloatingSheet> {
  bool _expanded = false;

  void _toggleExpand() {
    if (!widget.expandable) return;
    setState(() => _expanded = !_expanded);
  }

  @override
  Widget build(BuildContext context) {
    final reduceBlur = _maybeReduceBlur(context);
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final scheme = Theme.of(context).colorScheme;

    final sigma = AppGlass.effectiveBlur(AppGlass.blurHeavy, reduceBlur: reduceBlur);
    final tint = isDark
        ? Colors.black.withValues(alpha: 0.60)
        : Colors.white.withValues(alpha: 0.72);
    final dragBarColor = scheme.onSurface.withValues(alpha: 0.35);

    return LayoutBuilder(
      builder: (context, constraints) {
        final maxHeight = constraints.maxHeight;
        final fraction = _expanded ? widget.maxHeightFraction : widget.minHeightFraction;
        final height = maxHeight * fraction;

        return AnimatedContainer(
          duration: AppMotion.normal,
          curve: AppMotion.emphasized,
          height: height,
          alignment: Alignment.bottomCenter,
          child: ClipRRect(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
              child: Container(
                color: tint,
                child: SafeArea(
                  top: false,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // 拖拽条区域：点击切换半展开/全展开
                      GestureDetector(
                        key: const ValueKey('floatingSheetHandle'),
                        onTap: widget.expandable ? _toggleExpand : null,
                        behavior: HitTestBehavior.opaque,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          child: Container(
                            width: 40,
                            height: 4,
                            decoration: BoxDecoration(
                              color: dragBarColor,
                              borderRadius: BorderRadius.circular(2),
                            ),
                          ),
                        ),
                      ),
                      if (widget.title != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(
                            widget.title!,
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w600,
                              color: scheme.onSurface,
                            ),
                          ),
                        ),
                      Flexible(
                        child: SingleChildScrollView(
                          padding: const EdgeInsets.only(bottom: 16),
                          child: widget.child,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 弹出 `FloatingSheet` 的便捷入口（返回关闭时携带的结果）。
Future<T?> showFloatingSheet<T>({
  required BuildContext context,
  String? title,
  required Widget child,
  double maxHeightFraction = 0.85,
  double minHeightFraction = 0.35,
  bool expandable = true,
}) {
  return showModalBottomSheet<T>(
    context: context,
    backgroundColor: Colors.transparent,
    isScrollControlled: true,
    builder: (context) => FloatingSheet(
      title: title,
      maxHeightFraction: maxHeightFraction,
      minHeightFraction: minHeightFraction,
      expandable: expandable,
      child: child,
    ),
  );
}

/// 读取全局「降低模糊」开关，未包裹 Provider 的测试环境按不降级（false）兜底。
bool _maybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}
