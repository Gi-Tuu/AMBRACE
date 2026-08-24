import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'chat_group_chat_screen.dart';
import "package:ai_companion/widgets/app_page_route.dart";

/// 家庭群聊列表：查看/创建/删除群
class ChatGroupListScreen extends StatefulWidget {
  const ChatGroupListScreen({super.key});

  @override
  State<ChatGroupListScreen> createState() => _ChatGroupListScreenState();
}

class _ChatGroupListScreenState extends State<ChatGroupListScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _groups = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final groups = await _api.getChatGroups();
      if (mounted) {
        setState(() {
          _groups = groups;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _createGroup() async {
    List<AICharacter> chars = [];
    try {
      chars = await _api.getCharacters();
    } catch (_) {}
    if (!mounted) return;
    final selected = <int>{};
    final nameCtrl = TextEditingController();
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlgState) => AlertDialog(
          title: Text(AppLocalizations.of(ctx)!.createGroupDialog),
          content: SizedBox(
            width: 320,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TextField(
                  controller: nameCtrl,
                  decoration: InputDecoration(labelText: AppLocalizations.of(ctx)!.groupNameLabel, hintText: AppLocalizations.of(ctx)!.groupTitle),
                ),
                const SizedBox(height: 8),
                Text(AppLocalizations.of(ctx)!.selectMinTwo, style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 4),
                SizedBox(
                  height: 220,
                  child: chars.isEmpty
                      ? Center(child: Text(AppLocalizations.of(ctx)!.noChars))
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
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(AppLocalizations.of(ctx)!.cancel)),
            FilledButton(
              onPressed: selected.length >= 2
                  ? () async {
                      final name = nameCtrl.text.trim().isEmpty ? AppLocalizations.of(ctx)!.groupTitle : nameCtrl.text.trim();
                      await _api.createChatGroup(name, selected.toList());
                      if (ctx.mounted) Navigator.pop(ctx, true);
                    }
                  : null,
              child: Text(AppLocalizations.of(ctx)!.create),
            ),
          ],
        ),
      ),
    );
    if (created == true && mounted) {
      await _load();
    }
  }

  Future<void> _deleteGroup(int id) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(AppLocalizations.of(ctx)!.deleteGroup),
        content: Text(AppLocalizations.of(ctx)!.deleteGroupConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(AppLocalizations.of(ctx)!.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(AppLocalizations.of(ctx)!.delete)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteChatGroup(id);
      await _load();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.groupTitle),
        actions: [
          IconButton(icon: const Icon(Icons.add), tooltip: l10n.createGroup, onPressed: _createGroup),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _groups.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.forum_outlined, size: 48, color: Colors.grey),
                      const SizedBox(height: 8),
                      Text(l10n.noGroups, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: _createGroup, child: Text(l10n.createGroup)),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(12),
                  children: [
                    for (final g in _groups)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: IosCardGroup(children: [
                          ListTile(
                            title: Text(g['name'] as String? ?? l10n.groupTitle,
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                            subtitle: Text(
                              (g['members'] as List? ?? [])
                                  .map((m) => (m as Map)['name']?.toString() ?? '')
                                  .join('、'),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                            ),
                            trailing: IconButton(
                              icon: const Icon(Icons.delete_outline, size: 20),
                              onPressed: () => _deleteGroup(g['id'] as int),
                            ),
                            onTap: () => Navigator.push(
                              context,
                              AppPageRoute(
                                builder: (_) => ChatGroupChatScreen(
                                  groupId: g['id'] as int,
                                  groupName: g['name'] as String? ?? l10n.groupTitle,
                                ),
                              ),
                            ),
                          ),
                        ]),
                      ),
                  ],
                ),
    );
  }
}
