// 深拆（F7-c 收官，2026-09-01）自 screens/phone/phone_perception_screen.dart build 内局部闭包
// 提升为公共构件（纯展示，值经参数传入），逻辑逐字节保持。
import 'package:flutter/material.dart';

import 'package:ai_companion/theme/tokens.dart';

const Color _ppSubColor = AppColors.textSecondary;
const Color _ppIconColor = AppColors.accent;

/// 开关行（父开关关闭时置灰）
class PpSwitch extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool>? onChanged;
  final Color? color;

  const PpSwitch({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final enabled = onChanged != null;
    return SwitchListTile(
      secondary: Icon(icon,
          size: 22,
          color: enabled ? (color ?? _ppIconColor) : scheme.onSurface.withValues(alpha: 0.38)),
      title: Text(title,
          style: TextStyle(
              fontSize: 15,
              color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: _ppSubColor)),
      value: value,
      onChanged: onChanged,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14),
    );
  }
}

/// 导航行
class PpNav extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;
  final bool enabled;
  final Color? color;
  final Widget? trailing;

  const PpNav({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    this.onTap,
    this.enabled = true,
    this.color,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListTile(
      leading: Icon(icon, size: 22, color: color ?? _ppIconColor),
      title: Text(title, style: TextStyle(fontSize: 15, color: scheme.onSurface)),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: _ppSubColor)),
      enabled: enabled,
      trailing: trailing ?? const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14),
    );
  }
}

/// 可折叠父项：点击行展开/收起子项，右侧开关独立控制
class PpFoldParent extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool>? onChanged;
  final bool expanded;
  final VoidCallback onToggle;
  final Color? color;

  const PpFoldParent({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    required this.expanded,
    required this.onToggle,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final enabled = onChanged != null;
    return ListTile(
      leading: Icon(icon,
          size: 22,
          color: enabled ? (color ?? _ppIconColor) : scheme.onSurface.withValues(alpha: 0.38)),
      title: Text(title,
          style: TextStyle(
              fontSize: 15,
              color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: _ppSubColor)),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(expanded ? Icons.expand_less : Icons.expand_more,
              size: 20, color: AppColors.separator),
          Switch(value: value, onChanged: onChanged),
        ],
      ),
      onTap: onToggle,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14),
    );
  }
}

/// 分组卡片（title 可空）
class PpGroup extends StatelessWidget {
  final String? title;
  final List<Widget> children;

  const PpGroup({super.key, this.title, required this.children});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null)
            Padding(
              padding: const EdgeInsets.only(left: 16, bottom: 6),
              child: Text(title!,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: _ppSubColor)),
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

/// 组内分隔线
class PpDivider extends StatelessWidget {
  const PpDivider({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 0.5,
      margin: const EdgeInsets.only(left: 46),
      color: Theme.of(context).dividerColor,
    );
  }
}

/// R5：健康状态灯 tile
class PpHealthTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final bool ok;
  final String? sub;
  final VoidCallback? onTap;

  const PpHealthTile({
    super.key,
    required this.icon,
    required this.title,
    required this.ok,
    this.sub,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final subText = sub;
    return ListTile(
      leading: Icon(icon, size: 22, color: ok ? Colors.green : Colors.orange),
      title: Text(title, style: TextStyle(fontSize: 15, color: scheme.onSurface)),
      subtitle: subText == null
          ? null
          : Text(subText, style: const TextStyle(fontSize: 11, color: _ppSubColor)),
      trailing: onTap != null
          ? const Icon(Icons.chevron_right, size: 18, color: AppColors.separator)
          : Icon(ok ? Icons.check_circle : Icons.warning_amber,
              size: 18, color: ok ? Colors.green : Colors.orange),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14),
    );
  }
}
