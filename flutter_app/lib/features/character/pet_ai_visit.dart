// F7-c-1（2026-08-31）自 features/character/pet_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../theme/tokens.dart';
import '../../services/api_client.dart';
import 'pet_adopt_views.dart' show adoptableSpecies;

/// 拜访面板：列出角色及其 AI 宠物（互动 / 代为领养）
class AiPetPanel extends StatefulWidget {
  final List<Map<String, dynamic>> characters;
  final void Function() onChanged;
  const AiPetPanel({super.key, required this.characters, required this.onChanged});

  @override
  State<AiPetPanel> createState() => AiPetPanelState();
}

class AiPetPanelState extends State<AiPetPanel> {
  late List<Map<String, dynamic>> _chars = List.of(widget.characters);
  bool _busy = false;

  Future<void> _refresh() async {
    try {
      final chars = await ApiClient().getAiPets();
      if (mounted) setState(() => _chars = chars);
    } catch (_) {}
  }

  Future<void> _interact(Map<String, dynamic> pet, String action, String label) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      await ApiClient().petAction((pet['id'] as num).toInt(), action);
      widget.onChanged();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${pet['name']}：${l10n.actionSucceeded(label)}')));
      }
      await _refresh();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.interactFailed)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _adopt(Map<String, dynamic> c) async {
    final l10n = AppLocalizations.of(context)!;
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => AiAdoptDialog(characterName: c['character_name'] as String? ?? 'TA'),
    );
    if (result == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await ApiClient().aiAdopt(
        (c['character_id'] as num).toInt(),
        result['species'] as String,
        result['name'] as String,
      );
      widget.onChanged();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.adoptForChar(c['character_name'] as String? ?? 'TA'))));
      }
      await _refresh();
    } catch (e) {
      final msg = e.toString();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(msg.contains("已经养了宠物") ? msg : l10n.adoptFailedRetry)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    return SafeArea(
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        maxChildSize: 0.92,
        builder: (ctx, scrollCtrl) => ListView(
          controller: scrollCtrl,
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
          children: [
            Text(l10n.aiPetsTitle, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(l10n.aiPetsSubtitle,
                style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
            const SizedBox(height: 12),
            if (_busy) const LinearProgressIndicator(minHeight: 2),
            const SizedBox(height: 8),
            for (final c in _chars) ...[
              _buildCharCard(c),
              const SizedBox(height: 12),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildCharCard(Map<String, dynamic> c) {
    final l10n = AppLocalizations.of(context)!;
    final pet = c['pet'] as Map<String, dynamic>?;
    final charName = c['character_name'] as String? ?? 'TA';
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: pet == null
            ? Row(
                children: [
                  Expanded(
                    child: Text(l10n.noPetForChar(charName), style: const TextStyle(fontSize: 14)),
                  ),
                  FilledButton.tonal(
                    onPressed: _busy ? null : () => _adopt(c),
                    child: Text(l10n.adoptForTa),
                  ),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.network(
                          ApiClient().resolveUrl(pet['avatar_url'] as String? ?? '/uploads/pets_assets/cat/idle.png'),
                          width: 44,
                          height: 44,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => const Icon(Icons.pets, size: 36, color: AppColors.textSecondary),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(l10n.charPetTitle(charName, pet['name'] as String? ?? ''),
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                            Text("${pet['species_label']} · ${pet['status_text']}",
                                style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    children: [
                      _actionChip(Icons.restaurant, l10n.feed, () => _interact(pet, 'feed', l10n.feed)),
                      _actionChip(Icons.sports_esports, l10n.play, () => _interact(pet, 'play', l10n.play)),
                      _actionChip(Icons.cleaning_services, l10n.clean, () => _interact(pet, 'clean', l10n.clean)),
                    ],
                  ),
                ],
              ),
      ),
    );
  }

  Widget _actionChip(IconData icon, String label, VoidCallback onTap) {
    return ActionChip(
      avatar: Icon(icon, size: 16),
      label: Text(label),
      visualDensity: VisualDensity.compact,
      onPressed: _busy ? null : onTap,
    );
  }
}

/// 代为领养对话框：选物种 + 起名（<=5 字）
class AiAdoptDialog extends StatefulWidget {
  final String characterName;
  const AiAdoptDialog({super.key, required this.characterName});

  @override
  State<AiAdoptDialog> createState() => AiAdoptDialogState();
}

class AiAdoptDialogState extends State<AiAdoptDialog> {
  final _nameCtrl = TextEditingController();
  String _species = 'cat';

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AlertDialog(
      title: Text(l10n.adoptPetFor(widget.characterName)),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final (sp, label) in adoptableSpecies(l10n))
                ChoiceChip(
                  label: Text(label),
                  selected: _species == sp,
                  onSelected: (_) => setState(() => _species = sp),
                ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _nameCtrl,
            maxLength: 5,
            decoration: InputDecoration(
              labelText: l10n.petNameLabel,
              isDense: true,
            ),
          ),
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
        FilledButton(
          onPressed: () {
            final name = _nameCtrl.text.trim();
            if (name.isEmpty) {
              ScaffoldMessenger.of(context)
                  .showSnackBar(SnackBar(content: Text(l10n.petNameRequired)));
              return;
            }
            Navigator.pop(context, {'species': _species, 'name': name});
          },
          child: Text(l10n.adopt),
        ),
      ],
    );
  }
}
