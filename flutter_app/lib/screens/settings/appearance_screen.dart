import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../theme/app_theme.dart';
import '../../theme/skins/skin.dart';
import '../../theme/skins/skin_registry.dart';
import '../../widgets/ios_card_group.dart';

/// 应用容貌页：皮肤 + 主题模式（跟随系统/浅色/深色）+ 主题色（6 款）+ 语言。
class AppearanceScreen extends StatelessWidget {
  const AppearanceScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final l10n = AppLocalizations.of(context)!;
    final brightness = Theme.of(context).brightness;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appearanceTitle)),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          // ── 皮肤选择 ──
          IosCardGroup(
            title: l10n.skinTitle,
            children: [
              SizedBox(
                height: 132,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  itemCount: SkinRegistry.all.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 14),
                  itemBuilder: (context, index) {
                    final skin = SkinRegistry.all[index];
                    final selected = settings.skinId == skin.id;
                    return _SkinOption(
                      skin: skin,
                      name: skinName(l10n, skin),
                      brightness: brightness,
                      seedColor: AppTheme.seedColorAt(settings.seedColorIndex),
                      selected: selected,
                      onTap: () => settings.setSkinId(skin.id),
                    );
                  },
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.themeMode,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: SegmentedButton<int>(
                  segments: [
                    ButtonSegment(value: 0, label: Text(l10n.followSystem), icon: const Icon(Icons.brightness_auto)),
                    ButtonSegment(value: 1, label: Text(l10n.light), icon: const Icon(Icons.light_mode_outlined)),
                    ButtonSegment(value: 2, label: Text(l10n.dark), icon: const Icon(Icons.dark_mode_outlined)),
                  ],
                  selected: {settings.themeModeIndex},
                  onSelectionChanged: (sel) => settings.setThemeModeIndex(sel.first),
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.themeColor,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  children: [
                    for (var i = 0; i < AppTheme.seedColors.length; i++)
                      _ColorOption(
                        color: AppTheme.seedColors[i],
                        name: _seedName(l10n, i),
                        selected: settings.seedColorIndex == i,
                        onTap: () => settings.setSeedColorIndex(i),
                      ),
                  ],
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.language,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: SegmentedButton<String>(
                  segments: [
                    ButtonSegment(value: 'system', label: Text(l10n.followSystem), icon: const Icon(Icons.language)),
                    ButtonSegment(value: 'zh', label: Text(l10n.simplifiedChinese)),
                    ButtonSegment(value: 'en', label: Text(l10n.english)),
                  ],
                  selected: {settings.localeCode},
                  onSelectionChanged: (sel) => settings.setLocale(sel.first),
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 20, top: 4, bottom: 16),
            child: Text(
              l10n.currentPreview(_modeName(l10n, settings.themeModeIndex), _seedName(l10n, settings.seedColorIndex)),
              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
            ),
          ),
        ],
      ),
    );
  }

  String _seedName(AppLocalizations l10n, int i) {
    switch (i) {
      case 0:
        return l10n.themeColorBlue;
      case 1:
        return l10n.themeColorPurple;
      case 2:
        return l10n.themeColorPink;
      case 3:
        return l10n.themeColorCyan;
      case 4:
        return l10n.themeColorGreen;
      default:
        return l10n.themeColorOrange;
    }
  }

  String _modeName(AppLocalizations l10n, int index) {
    switch (index) {
      case 1:
        return l10n.light;
      case 2:
        return l10n.dark;
      default:
        return l10n.followSystem;
    }
  }

  /// 皮肤名 l10n 解析（按 skin.id 映射；未知 id 回退到 displayName）
  static String skinName(AppLocalizations l10n, Skin skin) {
    switch (skin.id) {
      case 'ios':
        return l10n.skinNameIos;
      case 'warm':
        return l10n.skinNameWarm;
      case 'material':
        return l10n.skinNameMaterial;
      case 'paper':
        return l10n.skinNamePaper;
      case 'neon':
        return l10n.skinNameNeon;
      default:
        return skin.displayName;
    }
  }
}

/// 皮肤选项卡：迷你聊天气泡预览 + 名称 + 选中勾
class _SkinOption extends StatelessWidget {
  final Skin skin;
  final String name;
  final Brightness brightness;
  final Color seedColor;
  final bool selected;
  final VoidCallback onTap;
  const _SkinOption({
    required this.skin,
    required this.name,
    required this.brightness,
    required this.seedColor,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    // 不支持深色的皮肤在深色下用浅色预览（实际运行时也会回退）
    final effectiveBrightness =
        (brightness == Brightness.dark && !skin.supportsDarkMode) ? Brightness.light : brightness;
    final themeData = skin.buildThemeData(brightness: effectiveBrightness, seedColor: seedColor);
    final colors = skin.buildSkinColors(brightness: effectiveBrightness, seedColor: seedColor);
    final previewBg = themeData.scaffoldBackgroundColor;
    final userBubble = colors.bubbleUser ?? themeData.colorScheme.primaryContainer;
    final aiBubble = colors.bubbleAi ?? themeData.colorScheme.surfaceContainerHighest;
    final userText = colors.bubbleUserText ?? themeData.colorScheme.onPrimaryContainer;
    final aiText = colors.bubbleAiText ?? themeData.colorScheme.onSurfaceVariant;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        width: 104,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: previewBg,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: selected ? Theme.of(context).colorScheme.primary : Theme.of(context).dividerColor.withValues(alpha: 0.5),
            width: selected ? 2.5 : 1,
          ),
          boxShadow: selected
              ? [BoxShadow(color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.18), blurRadius: 8, offset: const Offset(0, 2))]
              : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 迷你聊天预览
            Expanded(
              child: Stack(
                children: [
                  // AI 气泡（左）
                  Positioned(
                    top: 6,
                    left: 0,
                    child: _miniBubble(aiBubble, aiText, 38),
                  ),
                  // 用户气泡（右）
                  Positioned(
                    bottom: 6,
                    right: 0,
                    child: _miniBubble(userBubble, userText, 30),
                  ),
                  if (selected)
                    Positioned(
                      top: 0,
                      right: 0,
                      child: Container(
                        padding: const EdgeInsets.all(2),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.primary,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.check, color: Colors.white, size: 13),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 6),
            Text(
              name,
              style: TextStyle(
                fontSize: 12,
                fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                color: aiText,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }

  Widget _miniBubble(Color bg, Color fg, double width) {
    return Container(
      width: width,
      height: 16,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
      ),
      alignment: Alignment.center,
      child: Container(
        width: width * 0.55,
        height: 4,
        decoration: BoxDecoration(
          color: fg.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}

class _ColorOption extends StatelessWidget {
  final Color color;
  final String name;
  final bool selected;
  final VoidCallback onTap;
  const _ColorOption({required this.color, required this.name, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
              border: Border.all(
                color: selected ? Theme.of(context).colorScheme.onSurface : Colors.transparent,
                width: 3,
              ),
            ),
            child: selected
                ? const Icon(Icons.check, color: Colors.white, size: 22)
                : null,
          ),
          const SizedBox(height: 4),
          Text(name, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }
}
