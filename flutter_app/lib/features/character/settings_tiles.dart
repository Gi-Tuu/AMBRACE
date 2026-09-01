// 深拆（F7-c-7，2026-09-01）自 screens/character/character_settings_screen.dart 迁入；
// 通用行构件（纯展示，状态经回调上抛），逻辑逐字节保持。
import 'package:flutter/material.dart';

import 'package:ai_companion/theme/tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/ios_card_group.dart';

/// Aurora P5 分组：AuroraCard 版 IosCardGroup（标题视觉保留；透明 Material 防组内开关断言）
Widget auroraGroup({required String title, required List<Widget> children}) {
  return Padding(
    padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(left: 16, bottom: 6),
          child: Text(title,
              style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: IosCardColors.subtitle)),
        ),
        AuroraCard(
          padding: EdgeInsets.zero,
          child: Material(
            type: MaterialType.transparency,
            child: Column(children: children),
          ),
        ),
      ],
    ),
  );
}

/// Aurora P5 行图标：40×40 圆角 12 容器（主题色 0.10~0.14 底，激活时图标主题色）
Widget settingsRowIcon(BuildContext context, IconData icon, {required bool active}) {
  final scheme = Theme.of(context).colorScheme;
  return Container(
    width: 40,
    height: 40,
    decoration: BoxDecoration(
      color: scheme.primary.withValues(alpha: active ? 0.14 : 0.10),
      borderRadius: BorderRadius.circular(12),
    ),
    child: Icon(icon, size: 22, color: active ? scheme.primary : IosCardColors.subtitle),
  );
}

/// 普通开关行
class GroupSwitchTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  const GroupSwitchTile({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => onChanged(!value),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
          children: [
            settingsRowIcon(context, icon, active: value),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w500, color: Theme.of(context).colorScheme.onSurface)),
                  const SizedBox(height: 1),
                  Text(subtitle,
                      style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                ],
              ),
            ),
            Switch(value: value, onChanged: onChanged),
          ],
        ),
      ),
    );
  }
}

/// 子开关行（父开关关闭时 onChanged 为 null：标题自动灰化）
class ChildSwitch extends StatelessWidget {
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool>? onChanged;

  const ChildSwitch({
    super.key,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final enabled = onChanged != null;
    return Padding(
      padding: const EdgeInsets.only(top: 2, bottom: 2),
      child: SwitchListTile(
        contentPadding: const EdgeInsets.only(left: 8),
        title: Text(title,
            style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        value: value,
        onChanged: onChanged,
        activeThumbColor: Theme.of(context).colorScheme.primary,
      ),
    );
  }
}

/// 可展开的父开关（点击开关切换、点击行展开子项；开关左侧带无柄箭头，仿手机感知）。
/// [expanded]/[onExpansionToggle] 由屏幕 State 持有（key=标题）。
class ExpansionSwitch extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool>? onChanged;
  final bool expanded;
  final void Function(String title, bool expanded) onExpansionToggle;
  final List<Widget> children;

  const ExpansionSwitch({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    required this.expanded,
    required this.onExpansionToggle,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isOpen = expanded;
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        leading: settingsRowIcon(context, icon, active: value),
        title: Text(title,
            style: TextStyle(
                fontSize: 15, fontWeight: FontWeight.w500, color: scheme.onSurface)),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        childrenPadding: const EdgeInsets.only(left: 16, right: 16, bottom: 8),
        tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 0),
        shape: const Border(),
        collapsedShape: const Border(),
        onExpansionChanged: (v) => onExpansionToggle(title, v),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(isOpen ? Icons.expand_less : Icons.expand_more,
                size: 20, color: AppColors.separator),
            Switch(value: value, onChanged: onChanged),
          ],
        ),
        children: children,
      ),
    );
  }
}

/// 免打扰时段行：点击弹出时间选择（选择器在屏幕 State 的 onPickTime 中）
class TimeRow extends StatelessWidget {
  final String label;
  final String value;
  final String field;
  final Future<void> Function(String current, String field) onPickTime;

  const TimeRow({
    super.key,
    required this.label,
    required this.value,
    required this.field,
    required this.onPickTime,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(left: 8, top: 4, bottom: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => onPickTime(value, field),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          child: Row(
            children: [
              Text(label,
                  style: const TextStyle(fontSize: 14, color: IosCardColors.subtitle)),
              const Spacer(),
              Text(value,
                  style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                      color: scheme.onSurface)),
              const SizedBox(width: 4),
              Icon(Icons.chevron_right, size: 18, color: scheme.onSurface.withValues(alpha: 0.4)),
            ],
          ),
        ),
      ),
    );
  }
}
