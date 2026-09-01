// F7-c-2（2026-08-31）自 screens/phone/ai_interaction_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../providers/settings_provider.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/aurora_card.dart';

class PhoneTile extends StatelessWidget {
  const PhoneTile({super.key, 
    required this.character,
    required this.chats,
    this.present,
    required this.onTap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  /// AI 此刻（Phase D，2026-08-14）：{phase, mood}
  final Map<String, dynamic>? present;
  final VoidCallback onTap;

  /// 角色卡片上的「此刻」精简状态行（present 为空时不显示）
  Widget _presentLine(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final p = present;
    if (p == null) return const SizedBox.shrink();
    final doing = switch (p['phase']) {
      'sleep' => l10n.phaseSleep,
      'morning' => l10n.phaseMorning,
      'afternoon' => l10n.phaseAfternoon,
      'evening' => l10n.phaseEvening,
      _ => l10n.phaseLiving,
    };
    final mood = (p['mood'] as int?) ?? 50;
    final moodText = mood >= 70
        ? l10n.moodGreat
        : mood >= 50
            ? l10n.moodGood
            : mood >= 30
                ? l10n.moodOk
                : l10n.moodLow;
    return Text(
      l10n.presentLine(doing, moodText),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(fontSize: 10, color: Theme.of(context).colorScheme.primary),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;
    final hasChats = chats.isNotEmpty;
    final last = hasChats ? chats.last : null;
    // Aurora P2：容器换 AuroraCard（onTap 启用内置按压 0.98，reduceMotion 自带不缩放；
    // 列表内不做 BackdropFilter）
    return Opacity(
      opacity: hasChats ? 1.0 : 0.55,
      child: AuroraCard(
        onTap: onTap,
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
        child: Column(
              children: [
                // 听筒
                Container(
                  width: 34,
                  height: 4,
                  decoration: BoxDecoration(
                    color: theme.colorScheme.outlineVariant,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(height: 10),
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      AIAvatar(
                        name: character.name,
                        size: 52,
                        imageUrl: character.avatarUrl,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        character.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        last != null ? last.content : l10n.noChats,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: theme.colorScheme.onSurfaceVariant),
                      ),
                      const SizedBox(height: 3),
                      _presentLine(context),
                    ],
                  ),
                ),

              ],
            ),
      ),
    );
  }
}

/// 该角色的"内置畅聊"首页：私信会话 + 家庭群聊会话（同一列表，右上角创建群聊）

class IconPressScale extends StatefulWidget {
  final bool enabled;
  final Widget child;

  const IconPressScale({super.key, required this.enabled, required this.child});

  @override
  State<IconPressScale> createState() => IconPressScaleState();
}

class IconPressScaleState extends State<IconPressScale> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerDown: widget.enabled ? (_) => setState(() => _pressed = true) : null,
      onPointerUp: widget.enabled ? (_) => setState(() => _pressed = false) : null,
      onPointerCancel: widget.enabled ? (_) => setState(() => _pressed = false) : null,
      child: TweenAnimationBuilder<double>(
        tween: Tween(end: _pressed ? 0.9 : 1.0),
        duration: AppMotion.fast,
        curve: AppMotion.emphasized,
        builder: (context, scale, child) =>
            Transform.scale(scale: scale, child: child),
        child: widget.child,
      ),
    );
  }
}

/// 读取全局「降低模糊」开关；未包裹 Provider 时按不降级（false）兜底（仿 AuroraCard）。
bool maybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}

/// 读取全局「降低动效」开关；未包裹 Provider 时按不降级（false）兜底。
bool maybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}
