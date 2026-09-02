// F7-c-4a（2026-08-31）自 features/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import 'app_settings.dart' show appIcon, appSubtitle;

/// 小手机应用页集合：相册 / 应用市场 / 日历 / 浏览器 / 主题 / 设置（2026-08-11）

/// Aurora P3 统一玻璃 AppBar（手机内页模式：半透明底 + 0.5px 描边，无 BackdropFilter）
AppBar _phoneGlassAppBar(
  BuildContext context, {
  Widget? title,
  List<Widget> actions = const [],
  PreferredSizeWidget? bottom,
}) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  return AppBar(
    backgroundColor: isDark
        ? Colors.black.withValues(alpha: 0.30)
        : Colors.white.withValues(alpha: 0.55),
    elevation: 0,
    scrolledUnderElevation: 0,
    surfaceTintColor: Colors.transparent,
    shape: Border(
      bottom: BorderSide(
        color: isDark
            ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
            : Colors.black.withValues(alpha: AppGlass.borderAlpha),
        width: 0.5,
      ),
    ),
    title: title,
    actions: actions,
    bottom: bottom,
  );
}

// ── 相册：AI 生成图片 + 用户上传（iOS 图库风格：网格 → 全屏预览，保存/删除） ──
class MarketScreen extends StatelessWidget {
  const MarketScreen({
    super.key,
    required this.catalog,
    required this.installedKeys,
    required this.onRestore,
  });

  final List<Map<String, dynamic>> catalog; // [{key,label,deletable,plugin?}]
  final Set<String> installedKeys; // 桌面当前可见应用 key
  final Future<void> Function(String key) onRestore;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.marketTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(l10n.marketHint,
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)),
          const SizedBox(height: 12),
          // Aurora P3：ListTile → AuroraCard 行（46px 图标容器 + 安装态/下载按钮）
          for (final app in catalog)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: AuroraCard(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    Container(
                      width: 46,
                      height: 46,
                      decoration: BoxDecoration(
                        color: scheme.primaryContainer,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(appIcon(app['key'] as String), color: scheme.primary),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(app['label'] as String? ?? app['key'] as String,
                              maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600)),
                          const SizedBox(height: 2),
                          Text(appSubtitle(app, l10n),
                              maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                  fontSize: 12, color: scheme.onSurfaceVariant)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (installedKeys.contains(app['key']))
                      Text(l10n.installed,
                          style: TextStyle(color: scheme.onSurfaceVariant))
                    else
                      FilledButton(
                        onPressed: () async {
                          await onRestore(app['key'] as String);
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text(l10n.restoredToDesktop)),
                            );
                          }
                        },
                        child: Text(l10n.download),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

// ── 日历：月视图 + 备注（AI 可查看/写备注） ──
