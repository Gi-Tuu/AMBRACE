// F7-c-4a（2026-08-31）自 screens/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:intl/intl.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';

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
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.settingsTitle)),
      // Aurora P3：占位内容包 AuroraCard（文案零改动）
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: AuroraCard(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.phone_android, size: 40, color: scheme.primary),
              const SizedBox(height: 12),
              Text(l10n.virtualPhone,
                  style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: scheme.onSurface)),
              const SizedBox(height: 8),
              Text(
                l10n.virtualPhoneDesc,
                style: TextStyle(
                    fontSize: 13, height: 1.5, color: scheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── 共享小工具 ──
IconData appIcon(String key) {
  switch (key) {
    case 'chat':
      return Icons.chat_bubble;
    case 'album':
      return Icons.photo_library;
    case 'market':
      return Icons.storefront;
    case 'calendar':
      return Icons.calendar_month;
    case 'browser':
      return Icons.public;
    case 'theme':
      return Icons.palette;
    case 'settings':
      return Icons.settings;
    default:
      return Icons.apps;
  }
}

String appSubtitle(Map<String, dynamic> app, AppLocalizations l10n) {
  switch (app['key']) {
    case 'browser':
      return l10n.appDescBrowser;
    case 'album':
      return l10n.appDescAlbum;
    case 'market':
      return l10n.appDescMarket;
    case 'calendar':
      return l10n.appDescCalendar;
    case 'theme':
      return l10n.appDescTheme;
    case 'settings':
      return l10n.appDescSettings;
    case 'chat':
      return l10n.appDescChat;
    case 'memo':
      return l10n.appDescMemo;
    default:
      return '';
  }
}

String fmtTime(String iso) {
  try {
    final dt = DateTime.parse(iso).toLocal();
    final now = DateTime.now();
    final fmt = DateFormat(now.difference(dt).inDays < 1 ? 'HH:mm' : 'MM-dd HH:mm');
    return fmt.format(dt);
  } catch (_) {
    return '';
  }
}
