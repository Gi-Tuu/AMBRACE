// F7-c-1（2026-08-31）自 features/character/pet_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../theme/aurora_tokens.dart';
import '../../theme/tokens.dart';
import '../../utils/beijing_time.dart';

/// Aurora P4：底部悬浮毛玻璃胶囊操作栏（喂食 / 玩耍 / 清洁）。
/// BackdropFilter sigma 经 AppGlass.effectiveBlur（全局 reduceBlur 生效）；
/// 与互动区点击完全等价（同走 _interact），供无障碍与快捷操作。
class PetActionBar extends StatelessWidget {
  final Future<void> Function(String action) onInteract;
  const PetActionBar({super.key, required this.onInteract});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final reduceBlur = petMaybeReduceBlur(context);
    final sigma = AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: reduceBlur);
    final barColor = isDark
        ? Colors.black.withValues(alpha: 0.30)
        : Colors.white.withValues(alpha: 0.55);
    final borderColor = isDark
        ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
        : Colors.black.withValues(alpha: AppGlass.borderAlpha);

    Widget button(IconData icon, String tooltip, String action) => Tooltip(
          message: tooltip,
          child: InkWell(
            onTap: () => onInteract(action),
            borderRadius: BorderRadius.circular(24),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
              child: Icon(icon, size: 24, color: scheme.primary),
            ),
          ),
        );

    return SafeArea(
      top: false,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(32),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
              child: Container(
                decoration: BoxDecoration(
                  color: barColor,
                  borderRadius: BorderRadius.circular(32),
                  border: Border.all(color: borderColor, width: 0.5),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    button(Icons.restaurant, l10n.feed, 'feed'),
                    button(Icons.toys, l10n.play, 'play'),
                    button(Icons.cleaning_services, l10n.clean, 'clean'),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 读取全局「降低模糊」开关；未包裹 Provider 的环境按不降级（false）兜底。
bool petMaybeReduceBlur(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceBlur;
  } catch (_) {
    return false;
  }
}

/// 读取全局「降低动效」开关；未包裹 Provider 的环境按不降级（false）兜底。
bool petMaybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}

/// 食物气泡：宠物身边的食物，点击视为喂食
class FoodBubble extends StatelessWidget {
  const FoodBubble({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 56,
      height: 56,
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.warning),
      ),
      child: const Center(
        child: Text("🍖", style: TextStyle(fontSize: 28)),
      ),
    );
  }
}

/// 互动记录行：内容 + 北京时间
class ActivityRow extends StatelessWidget {
  final Map<String, dynamic> act;
  const ActivityRow({super.key, required this.act});

  @override
  Widget build(BuildContext context) {
    final content = act['content'] as String? ?? "";
    final createdAt = act['created_at'] as String? ?? "";
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text("· ", style: TextStyle(fontSize: 13, color: AppColors.textSecondary)),
          Expanded(
            child: Text(content, style: const TextStyle(fontSize: 13)),
          ),
          const SizedBox(width: 8),
          Text(
            _shortTime(createdAt),
            style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
          ),
        ],
      ),
    );
  }

  /// UTC -> 北京时间 "MM-dd HH:mm"
  String _shortTime(String iso) {
    if (iso.length < 19) return "";
    try {
      final bj = formatBeijingTime(iso);
      return bj.length >= 16 ? bj.substring(5, 16) : bj;
    } catch (_) {
      return "";
    }
  }
}

