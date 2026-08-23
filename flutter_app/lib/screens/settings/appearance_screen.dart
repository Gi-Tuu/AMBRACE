import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../theme/app_theme.dart';
import '../../widgets/ios_card_group.dart';

/// 应用容貌页：主题模式（跟随系统/浅色/深色）+ 主题色（6 款）+ 语言（跟随系统/简体中文/English）。
class AppearanceScreen extends StatelessWidget {
  const AppearanceScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appearanceTitle)),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
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
                        name: AppTheme.seedNames[i],
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
              l10n.currentPreview(AppTheme.modeName(settings.themeModeIndex), AppTheme.seedNames[settings.seedColorIndex]),
              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
            ),
          ),
        ],
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
