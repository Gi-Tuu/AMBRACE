import 'package:flutter/material.dart';
import "package:ai_companion/theme/tokens.dart";
import 'package:provider/provider.dart';
import '../providers/settings_provider.dart';
import '../theme/skins/skin_registry.dart';

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
    // 只让新皮肤 aegean 长金边；其余 6 款（含 paper）维持现状、像素零变化
    final settings = context.read<SettingsProvider>();
    final deco = SkinRegistry.get(settings.skinId).decoration;
    final hasFrame = settings.skinId == 'aegean';
    final radius = BorderRadius.circular(hasFrame ? deco.cardRadius : 12);

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
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  // 典藏皮肤：分组小标题用古金并略加字距；其它皮肤维持原灰
                  color: hasFrame ? scheme.primary : IosCardColors.subtitle,
                  letterSpacing: hasFrame ? 0.8 : 0,
                ),
              ),
            ),
          Container(
            decoration: BoxDecoration(
              color: scheme.surface,
              borderRadius: radius,
              border: hasFrame
                  ? Border.all(
                      color: deco.borderColor ?? Colors.transparent,
                      width: deco.borderWidth <= 0 ? 0.8 : deco.borderWidth,
                    )
                  : null,
            ),
            // 透明 Material：组内 ListTile/SwitchListTile 需要最近的 Material 祖先，
            // 否则 debug 断言报「背景色/墨水效果被 DecoratedBox 遮挡」（视觉零变化）
            child: Material(
              type: MaterialType.transparency,
              child: Column(children: children),
            ),
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
  static const subtitle = AppColors.textSecondary;
  static const chevron = AppColors.separator;

  IosCardColors._();
}
