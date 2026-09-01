// 深拆（F7-c-7，2026-09-01）自 screens/character/character_settings_screen.dart 迁入；
// 「社交 / 隐私 / 状态 / 记忆轨迹」四组 section（值经参数传入、变更经 onFieldChanged 上抛）。
import 'package:flutter/material.dart';

import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/tokens.dart';

import '../../screens/character/memory_trace_screen.dart';
import '../../widgets/ios_card_group.dart';
import 'settings_tiles.dart';

/// 「社交」组：认知循环 / 织网全量注入 / 主动消息（频率+免打扰）/ 朋友圈
class SocialSection extends StatelessWidget {
  final bool cognitiveLoop;
  final ValueChanged<bool> onCognitiveLoopChanged;
  final bool weaveFullInject;
  final bool proactive;
  final bool moments;
  final bool momentsComment;
  final bool memoryReview;
  final String frequency;
  final bool dndEnabled;
  final String dndStart;
  final String dndEnd;
  final void Function(String field, dynamic value) onFieldChanged;
  final Map<String, bool> expanded;
  final void Function(String title, bool expanded) onExpansionToggle;
  final Future<void> Function(String current, String field) onPickTime;

  const SocialSection({
    super.key,
    required this.cognitiveLoop,
    required this.onCognitiveLoopChanged,
    required this.weaveFullInject,
    required this.proactive,
    required this.moments,
    required this.momentsComment,
    required this.memoryReview,
    required this.frequency,
    required this.dndEnabled,
    required this.dndStart,
    required this.dndEnd,
    required this.onFieldChanged,
    required this.expanded,
    required this.onExpansionToggle,
    required this.onPickTime,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return auroraGroup(title: l10n.socialGroup, children: [
      GroupSwitchTile(
        icon: Icons.psychology_outlined,
        title: l10n.cognitiveLoop,
        subtitle: l10n.cognitiveLoopHint,
        value: cognitiveLoop,
        onChanged: onCognitiveLoopChanged,
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      GroupSwitchTile(
        icon: Icons.all_inclusive,
        title: l10n.weaveFullInject,
        subtitle: l10n.weaveFullInjectHint,
        value: weaveFullInject,
        onChanged: (v) => onFieldChanged('weave_full_inject_enabled', v),
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      ExpansionSwitch(
        icon: Icons.notifications_active_outlined,
        title: l10n.proactiveChat,
        subtitle: l10n.proactiveChatHint,
        value: proactive,
        onChanged: (v) => onFieldChanged('enable_proactive', v),
        expanded: expanded[l10n.proactiveChat] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.memoryReview,
            subtitle: l10n.memoryReviewHint,
            value: memoryReview && proactive,
            onChanged: proactive ? (v) => onFieldChanged('memory_review_enabled', v) : null,
          ),
          if (proactive) ...[
            Padding(
              padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(l10n.proactiveFrequency,
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                  const SizedBox(height: 2),
                  Text(l10n.proactiveFrequencyHint,
                      style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                  const SizedBox(height: 8),
                  SegmentedButton<String>(
                    segments: [
                      ButtonSegment(value: 'low', label: Text(l10n.lowFreq)),
                      ButtonSegment(value: 'medium', label: Text(l10n.standard)),
                      ButtonSegment(value: 'high', label: Text(l10n.highFreq)),
                    ],
                    selected: {frequency},
                    onSelectionChanged: (s) => onFieldChanged('frequency', s.first),
                    showSelectedIcon: false,
                  ),
                ],
              ),
            ),
            ChildSwitch(
              title: l10n.dndPeriod,
              subtitle: dndEnabled ? l10n.dndOn(dndStart, dndEnd) : l10n.dndOff,
              value: dndEnabled,
              onChanged: proactive ? (v) => onFieldChanged('dnd_enabled', v) : null,
            ),
            if (dndEnabled) ...[
              TimeRow(label: l10n.start, value: dndStart, field: 'dnd_start', onPickTime: onPickTime),
              TimeRow(label: l10n.end, value: dndEnd, field: 'dnd_end', onPickTime: onPickTime),
            ],
          ],
        ],
      ),
      Divider(height: 1, indent: 52, color: scheme.outlineVariant),
      ExpansionSwitch(
        icon: Icons.people_outline,
        title: l10n.moments,
        subtitle: l10n.momentsHint,
        value: moments,
        onChanged: (v) => onFieldChanged('moments_enabled', v),
        expanded: expanded[l10n.moments] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.momentsComment,
            subtitle: l10n.momentsCommentHint,
            value: momentsComment && moments,
            onChanged: moments ? (v) => onFieldChanged('moments_comment_enabled', v) : null,
          ),
        ],
      ),
    ]);
  }
}

/// 「隐私」组：隐私上锁 / 思考过程挡位 / 调用能力
class PrivacySection extends StatelessWidget {
  final bool privacyEnabled;
  final bool privacyLock;
  final bool showTools;
  final int reasoningLevel;
  final void Function(String field, dynamic value) onFieldChanged;
  final Map<String, bool> expanded;
  final void Function(String title, bool expanded) onExpansionToggle;

  const PrivacySection({
    super.key,
    required this.privacyEnabled,
    required this.privacyLock,
    required this.showTools,
    required this.reasoningLevel,
    required this.onFieldChanged,
    required this.expanded,
    required this.onExpansionToggle,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return auroraGroup(title: l10n.privacyGroup, children: [
      ExpansionSwitch(
        icon: Icons.lock_outline,
        title: l10n.privacy,
        subtitle: l10n.privacyHint,
        value: privacyEnabled,
        onChanged: (v) => onFieldChanged('privacy_enabled', v),
        expanded: expanded[l10n.privacy] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.privacyLock,
            subtitle: l10n.privacyLockHint,
            value: privacyLock,
            onChanged: privacyEnabled ? (v) => onFieldChanged('privacy_lock_enabled', v) : null,
          ),
          ChildSwitch(
            title: l10n.showTools,
            subtitle: l10n.showToolsHint,
            value: showTools,
            onChanged: privacyEnabled ? (v) => onFieldChanged('show_tools_enabled', v) : null,
          ),
          Padding(
            padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.reasoningLevel,
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                const SizedBox(height: 2),
                Text(l10n.reasoningLevelHint,
                    style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                const SizedBox(height: 8),
                SegmentedButton<int>(
                  segments: [
                    ButtonSegment(value: 0, label: Text(l10n.off), icon: const Icon(Icons.visibility_off_outlined, size: 16)),
                    ButtonSegment(value: 1, label: Text(l10n.simpleThinking), icon: const Icon(Icons.lightbulb_outline, size: 16)),
                    ButtonSegment(value: 2, label: Text(l10n.deepThinking), icon: const Icon(Icons.psychology_outlined, size: 16)),
                  ],
                  selected: {reasoningLevel},
                  onSelectionChanged: privacyEnabled
                      ? (s) => onFieldChanged('reasoning_level', s.first)
                      : null,
                  showSelectedIcon: false,
                  style: const ButtonStyle(visualDensity: VisualDensity.compact),
                ),
              ],
            ),
          ),
        ],
      ),
    ]);
  }
}

/// 「状态」组：状态触发 → 冷战断联 / 心情标识
class StatusSection extends StatelessWidget {
  final bool stateTrigger;
  final ValueChanged<bool> onStateChanged;
  final bool coldWar;
  final bool moodBadge;
  final void Function(String field, dynamic value) onFieldChanged;
  final Map<String, bool> expanded;
  final void Function(String title, bool expanded) onExpansionToggle;

  const StatusSection({
    super.key,
    required this.stateTrigger,
    required this.onStateChanged,
    required this.coldWar,
    required this.moodBadge,
    required this.onFieldChanged,
    required this.expanded,
    required this.onExpansionToggle,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return auroraGroup(title: l10n.statusGroup, children: [
      ExpansionSwitch(
        icon: Icons.mood_bad_outlined,
        title: l10n.status,
        subtitle: l10n.statusHint,
        value: stateTrigger,
        onChanged: onStateChanged,
        expanded: expanded[l10n.status] ?? false,
        onExpansionToggle: onExpansionToggle,
        children: [
          ChildSwitch(
            title: l10n.stateTrigger,
            subtitle: l10n.stateTriggerHint,
            value: stateTrigger,
            onChanged: stateTrigger ? onStateChanged : null,
          ),
          ChildSwitch(
            title: l10n.coldWar,
            subtitle: l10n.coldWarHint,
            value: coldWar && stateTrigger,
            onChanged: (stateTrigger) ? (v) => onFieldChanged('cold_war_enabled', v) : null,
          ),
          ChildSwitch(
            title: l10n.moodBadge,
            subtitle: l10n.moodBadgeHint,
            value: moodBadge,
            onChanged: (v) => onFieldChanged('mood_badge_enabled', v),
          ),
        ],
      ),
    ]);
  }
}

/// 「调试」组：记忆检索轨迹只读面板入口
class TraceSection extends StatelessWidget {
  final int characterId;
  final String characterName;

  const TraceSection({super.key, required this.characterId, required this.characterName});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return auroraGroup(title: l10n.memoryTraceGroup, children: [
      ListTile(
        leading: settingsRowIcon(context, Icons.memory, active: true),
        title: Text(l10n.memoryTraceTitle, style: const TextStyle(fontSize: 15)),
        subtitle: Text(l10n.memoryTraceEmpty,
            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        trailing: const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => MemoryTraceScreen(
              characterId: characterId,
              characterName: characterName,
            ),
          ),
        ),
      ),
    ]);
  }
}
