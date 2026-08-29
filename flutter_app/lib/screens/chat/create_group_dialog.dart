import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../models/character.dart';
import '../../services/api_client.dart';

/// 创建家庭群聊弹窗（复用）。返回 true=已创建，null/false=取消。
Future<bool?> showCreateGroupDialog(BuildContext context) async {
  final api = ApiClient();
  List<AICharacter> chars = [];
  try {
    chars = await api.getCharacters();
  } catch (_) {}
  if (!context.mounted) return null;

  final selected = <int>{};
  final nameCtrl = TextEditingController();
  final l10n = AppLocalizations.of(context)!;

  final ok = await showDialog<bool>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setDlgState) => AlertDialog(
        title: Text(l10n.createGroupDialog),
        content: SizedBox(
          width: 320,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextField(
                controller: nameCtrl,
                decoration: InputDecoration(
                  labelText: l10n.groupNameLabel,
                  hintText: l10n.groupTitle,
                ),
              ),
              const SizedBox(height: 8),
              Text(l10n.selectMinTwo, style: const TextStyle(fontSize: 13)),
              const SizedBox(height: 4),
              SizedBox(
                height: 220,
                child: chars.isEmpty
                    ? Center(child: Text(l10n.noChars))
                    : ListView(
                        children: [
                          for (final c in chars)
                            CheckboxListTile(
                              dense: true,
                              title: Text(c.name, style: const TextStyle(fontSize: 14)),
                              value: selected.contains(c.id),
                              onChanged: (v) => setDlgState(() {
                                if (v == true) {
                                  selected.add(c.id);
                                } else {
                                  selected.remove(c.id);
                                }
                              }),
                            ),
                        ],
                      ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: selected.length >= 2
                ? () async {
                    final name = nameCtrl.text.trim().isEmpty
                        ? l10n.groupTitle
                        : nameCtrl.text.trim();
                    try {
                      await api.createChatGroup(name, selected.toList());
                      if (ctx.mounted) Navigator.pop(ctx, true);
                    } catch (_) {
                      if (ctx.mounted) Navigator.pop(ctx, false);
                    }
                  }
                : null,
            child: Text(l10n.create),
          ),
        ],
      ),
    ),
  );
  nameCtrl.dispose();
  return ok;
}
