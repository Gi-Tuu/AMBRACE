import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/skins/aegean/aegean_architects.dart';
import '../theme/skins/aegean/aegean_palette.dart';

/// Aurora 空态 / 错误态 —— 统一空态展示。
///
/// 关键约束：
/// - **组件不内嵌任何用户可见文案**，全部由调用方传参，文案由调用方走 l10n。
/// - 居中布局：图标 64px（主题色 0.3 透明度）、标题 15 w600、副文案 13 textSecondary
///   最多 2 行、可选操作按钮。
class EmptyState extends StatelessWidget {
  /// 图标（使用方提供）。
  final IconData icon;

  /// 标题文案（使用方提供，走 l10n）。
  final String title;

  /// 副文案（可选，最多 2 行）。
  final String? subtitle;

  /// 可选操作按钮（如「去创建」「重试」）。
  final Widget? action;

  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.action,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // §6.6：aegean 空态用月桂枝替代默认图标、标题走衬线花体；其它皮肤零变化。
    final isAegean = Provider.of<SettingsProvider?>(context, listen: false)?.skinId == 'aegean';
    final b = Theme.of(context).brightness;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            isAegean
                ? SizedBox(
                    width: 44, height: 44,
                    child: AegeanWreath(
                      leafColor: AegeanPalette.goldDeep(b),
                      berryColor: AegeanPalette.terra(b),
                    ),
                  )
                : Icon(icon, size: 64, color: scheme.primary.withValues(alpha: 0.3)),
            const SizedBox(height: 16),
            Text(
              title,
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w600,
                color: scheme.onSurface,
                fontFamily: isAegean ? AegeanPalette.displayFont : null,
              ),
            ),
            if (subtitle != null) ...[
              const SizedBox(height: 8),
              Text(
                subtitle!,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 13, color: scheme.onSurfaceVariant),
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: 20),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}
