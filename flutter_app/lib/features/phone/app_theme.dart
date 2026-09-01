// F7-c-4a（2026-08-31）自 screens/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../services/api/phone_desktop_api.dart';

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
class ThemeScreen extends StatefulWidget {
  const ThemeScreen({
    super.key,
    required this.current,
    required this.onChanged,
  });
  final String? current; // null/空=默认；http=上传图；key=预置
  final Future<void> Function(String? wallpaper) onChanged;
  @override
  State<ThemeScreen> createState() => _ThemeScreenState();
}

class _ThemeScreenState extends State<ThemeScreen> {
  static const _presets = <Map<String, Object>>[
    {'key': '', 'c1': 0xFF1C1C3A, 'c2': 0xFF6C4E7E},
    {'key': 'aurora', 'c1': 0xFF0F3443, 'c2': 0xFF34E89E},
    {'key': 'sunset', 'c1': 0xFFC33764, 'c2': 0xFF1D2671},
    {'key': 'ocean', 'c1': 0xFF2193B0, 'c2': 0xFF6DD5ED},
    {'key': 'cherry', 'c1': 0xFFF7B3C6, 'c2': 0xFF6A5ACD},
    {'key': 'coffee', 'c1': 0xFF3E2723, 'c2': 0xFFB8860B},
  ];

  String? _selected;
  bool _saving = false;

  /// 预设壁纸名国际化（_presets 保持 const）
  String _presetLabel(Object? key) {
    final l10n = AppLocalizations.of(context)!;
    return switch (key) {
      null || '' => l10n.themeStarryNight,
      'aurora' => l10n.themeAurora,
      'sunset' => l10n.themeSunset,
      'ocean' => l10n.themeOcean,
      'cherry' => l10n.themeCherry,
      'coffee' => l10n.themeCoffee,
      _ => '',
    };
  }

  @override
  void initState() {
    super.initState();
    _selected = widget.current;
  }

  Future<void> _apply(String? value) async {
    setState(() {
      _saving = true;
      _selected = value;
    });
    try {
      await widget.onChanged(value);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.wallpaperChanged)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.saveFail)));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _upload() async {
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null || !mounted) return;
    try {
      final url = await ApiClient().uploadPhonePhoto(picked.path);
      await _apply(url);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.uploadFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.themeTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(l10n.wallpaper,
              style: TextStyle(
                  fontWeight: FontWeight.bold, color: scheme.onSurface)),
          const SizedBox(height: 8),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 10,
            crossAxisSpacing: 10,
            children: [
              for (final p in _presets)
                InkWell(
                  onTap: _saving ? null : () => _apply(p['key'] as String),
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: [
                          Color(p['c1'] as int),
                          Color(p['c2'] as int),
                        ],
                      ),
                      borderRadius: BorderRadius.circular(12),
                      border: _selected == p['key']
                          ? Border.all(color: Theme.of(context).colorScheme.primary, width: 3)
                          : null,
                    ),
                    child: Center(
                      child: Text(_presetLabel(p['key']),
                          style: const TextStyle(color: Colors.white, fontSize: 11)),
                    ),
                  ),
                ),
              InkWell(
                onTap: _saving ? null : _upload,
                borderRadius: BorderRadius.circular(12),
                child: Container(
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                    border: (_selected != null && (_selected!.startsWith('http')))
                        ? Border.all(color: Theme.of(context).colorScheme.primary, width: 3)
                        : null,
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.wallpaper, size: 26, color: Colors.grey),
                      const SizedBox(height: 4),
                      Text(l10n.uploadWallpaper, style: const TextStyle(fontSize: 11, color: Colors.grey)),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          Text(l10n.fontIconFuture,
              style: TextStyle(
                  fontWeight: FontWeight.bold, color: scheme.onSurface)),
          const SizedBox(height: 6),
          Text(l10n.fontIconHint,
              style: TextStyle(
                  fontSize: 12, color: scheme.onSurfaceVariant)),
        ],
      ),
    );
  }
}

// ── 备忘录：AI 与用户共同维护的便签 ──
