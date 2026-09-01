// 深拆（F7-c-7，2026-09-01）自 screens/character/character_settings_screen.dart 迁入；
// 「日常 / 创作 / 世界」三组 section（值经参数传入、变更经 onFieldChanged 上抛，级联在屏幕侧）。
import 'package:flutter/material.dart';

import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/tokens.dart';

import '../../screens/character/lorebook_screen.dart';
import '../../screens/character/world_settings_screen.dart';
import '../../widgets/ios_card_group.dart';
import 'settings_tiles.dart';

/// 「日常」组：AI 日记 / 线上生活（分享+强度）/ 打卡
class DailySection extends StatelessWidget {
  final bool diary;
  final bool lifeEnabled;
  final bool lifeShare;
  final String lifeIntensity;
  final bool checkIn;
  final void Function(String field, dynamic value) onFieldChanged;
  final Map<String, bool> expanded;
  final void Function(String title, bool expanded) onExpansionToggle;

  const DailySection({
    super.key,
    required this.diary,
    required this.lifeEnabled,
    required this.lifeShare,
    required this.lifeIntensity,
    required this.checkIn,
    required this.onFieldChanged,
    required this.expanded,
    required this.onExpansionToggle,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return auroraGroup(title: l10n.dailyGroup, children: [
      GroupSwitchTile(
        icon: Icons.book_outlined,
        title: l10n.aiDiary,
        subtitle: l10n.aiDiaryHint,
        value: diary,
        onChanged: (v) => onFieldChanged('diary_enabled', v),
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      ExpansionSwitch(
        icon: Icons.self_improvement_outlined,
        title: l10n.aiOfflineLife,
        subtitle: l10n.aiOfflineLifeHint,
        value: lifeEnabled,
        onChanged: (v) => onFieldChanged('life_enabled', v),
        expanded: expanded[l10n.aiOfflineLife] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.lifeShare,
            subtitle: l10n.lifeShareHint,
            value: lifeShare && lifeEnabled,
            onChanged: lifeEnabled ? (v) => onFieldChanged('life_share_enabled', v) : null,
          ),
          Padding(
            padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.lifeIntensity,
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                const SizedBox(height: 2),
                Text(l10n.lifeIntensityHint,
                    style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                const SizedBox(height: 8),
                SegmentedButton<String>(
                  segments: [
                    ButtonSegment(value: 'low', label: Text(l10n.low)),
                    ButtonSegment(value: 'medium', label: Text(l10n.medium)),
                    ButtonSegment(value: 'high', label: Text(l10n.high)),
                  ],
                  selected: {lifeIntensity},
                  onSelectionChanged: lifeEnabled
                      ? (sel) => onFieldChanged('life_intensity', sel.first)
                      : null,
                  showSelectedIcon: false,
                ),
              ],
            ),
          ),
        ],
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      ExpansionSwitch(
        icon: Icons.visibility_outlined,
        title: l10n.checkIn,
        subtitle: l10n.checkInHint,
        value: checkIn,
        onChanged: (v) => onFieldChanged('check_in_enabled', v),
        expanded: expanded[l10n.checkIn] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.control,
            subtitle: l10n.controlHint,
            value: false,
            onChanged: (v) {
              ScaffoldMessenger.of(context)
                ..hideCurrentSnackBar()
                ..showSnackBar(SnackBar(
                  content: Text(l10n.controlComingSoon),
                  duration: const Duration(seconds: 2),
                ));
            },
          ),
        ],
      ),
    ]);
  }
}

/// 「创作」组：生图（主动生图子开关）
class CreationSection extends StatelessWidget {
  final bool imageGen;
  final bool activeImageGen;
  final void Function(String field, dynamic value) onFieldChanged;
  final Map<String, bool> expanded;
  final void Function(String title, bool expanded) onExpansionToggle;

  const CreationSection({
    super.key,
    required this.imageGen,
    required this.activeImageGen,
    required this.onFieldChanged,
    required this.expanded,
    required this.onExpansionToggle,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return auroraGroup(title: l10n.creationGroup, children: [
      ExpansionSwitch(
        icon: Icons.auto_awesome,
        title: l10n.imageGen,
        subtitle: l10n.imageGenHint,
        value: imageGen,
        onChanged: (v) => onFieldChanged('image_gen_enabled', v),
        expanded: expanded[l10n.imageGen] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.activeImageGen,
            subtitle: l10n.activeImageGenHint,
            value: activeImageGen && imageGen,
            onChanged: imageGen ? (v) => onFieldChanged('active_image_gen_enabled', v) : null,
          ),
        ],
      ),
    ]);
  }
}

/// 「世界」组：织库 / 世界事实（两个导航入口）
class WorldSection extends StatelessWidget {
  final int characterId;

  const WorldSection({super.key, required this.characterId});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return auroraGroup(title: l10n.worldGroup, children: [
      ListTile(
        leading: settingsRowIcon(context, Icons.menu_book_outlined, active: true),
        title: Text(l10n.lorebookTitle, style: const TextStyle(fontSize: 15)),
        subtitle: Text(l10n.lorebookHint,
            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        trailing: const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => LorebookScreen(characterId: characterId)),
        ),
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      ListTile(
        leading: settingsRowIcon(context, Icons.public_outlined, active: true),
        title: Text(l10n.worldFactsTitle, style: const TextStyle(fontSize: 15)),
        subtitle: Text(l10n.worldFactsHint,
            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        trailing: const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => WorldSettingsScreen(characterId: characterId)),
        ),
      ),
    ]);
  }
}
