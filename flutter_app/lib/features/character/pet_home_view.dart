// F7-c-1（2026-08-31）自 features/character/pet_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../theme/tokens.dart';
import '../../models/pet.dart';
import '../../providers/pets_provider.dart';
import 'pet_fx.dart';
import 'pet_animated_pet.dart';

class PetHome extends StatefulWidget {
  final PetsProvider p;
  final void Function(PetsProvider p) onRename;
  final VoidCallback onVisit;
  final VoidCallback onAdopt;
  const PetHome({super.key, 
    required this.p,
    required this.onRename,
    required this.onVisit,
    required this.onAdopt,
  });

  @override
  State<PetHome> createState() => PetHomeState();
}

class PetHomeState extends State<PetHome> {
  String? _burstAction;
  int _burstSeq = 0;

  /// 满状态提示：四维已满时互动给出对应提示（不调接口）
  String? _fullHint(String action) {
    final l10n = AppLocalizations.of(context)!;
    final pet = widget.p.selectedPet;
    if (pet == null) return null;
    switch (action) {
      case "feed":
        return pet.hunger >= 100 ? l10n.petFullHunger : null;
      case "play":
        return pet.mood >= 100 && pet.energy >= 100 ? l10n.petFullPlay : null;
      case "clean":
        return pet.cleanliness >= 100 ? l10n.petFullClean : null;
    }
    return null;
  }

  Future<void> _interact(String action) async {
    final hint = _fullHint(action);
    if (hint != null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(hint)));
      }
      return;
    }
    setState(() {
      _burstAction = action;
      _burstSeq++;
    });
    final ok = await widget.p.interact(action);
    if (ok) await widget.p.refreshActivities();
  }

  /// 长按顶部宠物名片 -> 遗弃确认（硬删除；不影响角色与用户记忆，AI 会记得遗弃过）
  Future<void> _confirmAbandon(Pet pet) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.abandonTitle(pet.name)),
        content: Text(l10n.abandonConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.thinkAgain)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade400),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.abandon),
          ),
        ],
      ),
    );
    if (ok == true && mounted) {
      final success = await widget.p.abandon(pet.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(success ? l10n.abandoned(pet.name) : (widget.p.error ?? l10n.abandonFailed))),
        );
      }
    }
  }

  /// 清洁度每缺 10 点一坨大便（不满 10 点也算一坨）；满清洁度无大便
  int _poopCount(Pet pet) {
    if (pet.cleanliness >= 100) return 0;
    return ((100 - pet.cleanliness) ~/ 10) + 1;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final p = widget.p;
    final pet = p.selectedPet;
    if (pet == null) return Center(child: Text(l10n.noPets));
    final pets = p.pets;
    final poops = _poopCount(pet);
    return Column(
      children: [
        SizedBox(
          height: 44,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: [
              for (final item in pets)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: GestureDetector(
                    onLongPress: () => _confirmAbandon(item),
                    child: ChoiceChip(
                      label: Text(item.name),
                      selected: item.id == pet.id,
                      onSelected: (_) => p.selectPet(item.id),
                    ),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ActionChip(
                  avatar: const Icon(Icons.add, size: 18),
                  label: Text(l10n.adopt),
                  onPressed: widget.onAdopt,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              // 名称行：名字 + 铅笔图标（点击改名），拜访按钮同行最右侧
              Row(
                children: [
                  Expanded(
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Flexible(
                          child: Text(
                            pet.name,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: AppTypography.titleSize, fontWeight: AppTypography.titleWeight),
                          ),
                        ),
                        const SizedBox(width: 6),
                        GestureDetector(
                          onTap: () => widget.onRename(p),
                          child: Icon(Icons.edit, size: 18, color: AppColors.textSecondary),
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            "（${pet.speciesLabel}）",
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 13, color: AppColors.textMuted),
                          ),
                        ),
                      ],
                    ),
                  ),
                  OutlinedButton.icon(
                    onPressed: widget.onVisit,
                    icon: const Icon(Icons.door_front_door_outlined, size: 16),
                    label: Text(l10n.visit),
                    style: OutlinedButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                "Lv.${pet.level} · ${pet.statusText}",
                style: TextStyle(fontSize: 13, color: AppColors.textMuted),
              ),
              const SizedBox(height: 12),
              // 四维状态条（上移，为互动展示留出下方空间）
              StatBar(icon: Icons.restaurant, label: l10n.hunger, value: pet.hunger),
              StatBar(icon: Icons.sentiment_satisfied, label: l10n.mood, value: pet.mood),
              StatBar(icon: Icons.bolt, label: l10n.energy, value: pet.energy),
              StatBar(icon: Icons.cleaning_services, label: l10n.cleanliness, value: pet.cleanliness),
              const SizedBox(height: 8),
              // 互动区：点宠物玩耍 / 点食物喂食 / 点大便清洁
              Center(
                child: SizedBox(
                  width: 280,
                  height: 250,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      GestureDetector(
                        onTap: () => _interact("play"),
                        child: AnimatedPet(
                          pet: pet,
                          action: _burstAction,
                          actionSeq: _burstSeq,
                        ),
                      ),
                      // 食物（宠物身边，右下）
                      Positioned(
                        right: 2,
                        bottom: 10,
                        child: GestureDetector(
                          onTap: () => _interact("feed"),
                          child: const FoodBubble(),
                        ),
                      ),
                      // 大便（宠物身边，左下）
                      if (poops > 0)
                        Positioned(
                          left: 0,
                          bottom: 30,
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              for (var i = 0; i < poops; i++)
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 2),
                                  child: GestureDetector(
                                    onTap: () => _interact("clean"),
                                    child: const Text("💩", style: TextStyle(fontSize: 26)),
                                  ),
                                ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 4),
              Center(
                child: Text(
                  '${l10n.interactHintBase}${poops > 0 ? l10n.interactHintClean : ''}',
                  style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
                ),
              ),
              const SizedBox(height: 2),
              Center(
                child: Text(
                  l10n.longPressAbandon,
                  style: TextStyle(fontSize: 11, color: AppColors.textTertiary),
                ),
              ),
              const SizedBox(height: 18),
              // 互动展示区：任何角色/用户对宠物做了什么都会显示（短时重复仅算一件事）
              Row(
                children: [
                  Icon(Icons.history, size: 16, color: AppColors.textSecondary),
                  const SizedBox(width: 6),
                  Text(l10n.activityLog, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ],
              ),
              const SizedBox(height: 8),
              if (p.activities.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Center(
                    child: Text(
                      l10n.noActivities,
                      style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
                    ),
                  ),
                )
              else
                for (final act in p.activities.take(5)) ActivityRow(act: act),
            ],
          ),
        ),
        // Aurora P4：底部悬浮胶囊操作栏（喂食/玩耍/清洁，与互动区点击等价）；
        // 毛玻璃胶囊 = 全页唯一 BackdropFilter
        PetActionBar(onInteract: _interact),
      ],
    );
  }
}

