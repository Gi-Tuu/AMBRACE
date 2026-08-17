import 'package:flutter/material.dart';

/// UI 2.0 iOS 分组卡片：小标题 + 圆角卡片（浅色白卡 / 深色深卡自动适配）
class IosCardGroup extends StatelessWidget {
  final String? title;
  final List<Widget> children;
  final EdgeInsetsGeometry padding;

  const IosCardGroup({
    super.key,
    this.title,
    required this.children,
    this.padding = const EdgeInsets.only(left: 12, right: 12, bottom: 14),
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: padding,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null)
            Padding(
              padding: const EdgeInsets.only(left: 16, bottom: 6),
              child: Text(
                title!,
                style: const TextStyle(
                    fontSize: 12, fontWeight: FontWeight.w600, color: IosCardColors.subtitle),
              ),
            ),
          Container(
            decoration: BoxDecoration(
              color: scheme.surface,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(children: children),
          ),
        ],
      ),
    );
  }
}

/// 卡片内分隔线（iOS 缩进风格）
class IosCardDivider extends StatelessWidget {
  final double indent;

  const IosCardDivider({super.key, this.indent = 46});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 0.5,
      margin: EdgeInsets.only(left: indent),
      color: Theme.of(context).dividerColor,
    );
  }
}

/// iOS 通用色值（深浅色下均清晰）
class IosCardColors {
  static const subtitle = Color(0xFF8E8E93);
  static const chevron = Color(0xFFC6C6C8);

  IosCardColors._();
}
