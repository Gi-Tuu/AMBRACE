// F7-c-2（2026-08-31）自 screens/phone/ai_interaction_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../screens/chat/chat_group_chat_screen.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';
import 'phone_wechat_chat.dart' show WechatChatScreen, wechatGlassAppBar, wechatTime;

class WechatHomeScreen extends StatefulWidget {
  const WechatHomeScreen({super.key, 
    required this.character,
    required this.chats,
    required this.charMap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  final Map<int, AICharacter> charMap;

  @override
  State<WechatHomeScreen> createState() => WechatHomeScreenState();
}

class WechatHomeScreenState extends State<WechatHomeScreen> {
  List<Map<String, dynamic>> _groups = [];
  final Map<int, Map<String, dynamic>> _groupLast = {};
  final Map<int, int> _groupUnread = {};

  AICharacter get character => widget.character;
  Map<int, AICharacter> get charMap => widget.charMap;

  String _otherName(int otherId, AIChat first) =>
      charMap[otherId]?.name ??
      (first.characterAId == otherId ? first.characterAName : first.characterBName);

  /// 会话分组：对方角色 id -> 消息（时间正序），按最后消息时间倒序
  List<MapEntry<int, List<AIChat>>> _sessions() {
    final m = <int, List<AIChat>>{};
    for (final c in widget.chats) {
      final otherId =
          c.characterAId == character.id ? c.characterBId : c.characterAId;
      m.putIfAbsent(otherId, () => []).add(c);
    }
    final list = m.entries.toList();
    list.sort((x, y) => y.value.last.createdAt.compareTo(x.value.last.createdAt));
    return list;
  }

  /// 私聊未读数：本地已读标记之后的新消息数（进入会话后清除）
  Future<int> _unreadCount(List<AIChat> msgs, int otherId) async {
    final sp = await SharedPreferences.getInstance();
    final read = sp.getInt('wechat_read_${character.id}_$otherId') ?? 0;
    return msgs.where((c) => c.id > read).length;
  }

  Future<void> _loadGroups() async {
    try {
      final groups = await ApiClient().getChatGroups();
      final sp = await SharedPreferences.getInstance();
      final last = <int, Map<String, dynamic>>{};
      final unread = <int, int>{};
      for (final g in groups) {
        final gid = g['id'] as int;
        try {
          final msgs = await ApiClient().getChatGroupMessages(gid, limit: 100);
          last[gid] = msgs.isNotEmpty ? msgs.last : const {};
          final read = sp.getInt('group_read_$gid') ?? 0;
          unread[gid] =
              msgs.where((m) => ((m['id'] as num?)?.toInt() ?? 0) > read).length;
        } catch (_) {}
      }
      if (mounted) {
        setState(() {
          _groups = groups;
          _groupLast.addAll(last);
          _groupUnread.addAll(unread);
        });
      }
    } catch (_) {}
  }
  Future<void> _createGroup() async {
    final l10n = AppLocalizations.of(context)!;
    final chars = charMap.values.toList();
    if (chars.length < 2) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.needTwoChars)),
        );
      }
      return;
    }
    final selected = <int>{};
    final nameCtrl = TextEditingController();
    final created = await showDialog<bool>(
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
                  decoration: InputDecoration(labelText: l10n.groupNameLabel, hintText: l10n.groupTitle),
                ),
                const SizedBox(height: 8),
                Text(l10n.selectMinTwo, style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 4),
                SizedBox(
                  height: 220,
                  child: ListView(
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
                      final name = nameCtrl.text.trim().isEmpty ? l10n.groupTitle : nameCtrl.text.trim();
                      try {
                        await ApiClient().createChatGroup(name, selected.toList());
                      } catch (_) {}
                      if (ctx.mounted) Navigator.pop(ctx, true);
                    }
                  : null,
              child: Text(l10n.create),
            ),
          ],
        ),
      ),
    );
    nameCtrl.dispose();
    if (created == true && mounted) {
      await _loadGroups();
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
      await ApiClient().deleteChatGroup(id);
      await _loadGroups();
    } catch (_) {}
  }

  @override
  void initState() {
    super.initState();
    _loadGroups();
  }

  /// 统一会话项（私信 / 群聊），按最后消息时间倒序
  List<ChatEntry> _entries() {
    final list = <ChatEntry>[];
    for (final e in _sessions()) {
      list.add(ChatEntry.dm(
        otherId: e.key,
        msgs: e.value,
        other: charMap[e.key],
        otherName: _otherName(e.key, e.value.first),
      ));
    }
    for (final g in _groups) {
      list.add(ChatEntry.group(
        group: g,
        lastMsg: _groupLast[g['id'] as int],
      ));
    }
    list.sort((a, b) {
      final ta = a.lastTime;
      final tb = b.lastTime;
      if (ta == null && tb == null) return 0;
      if (ta == null) return 1;
      if (tb == null) return -1;
      return tb.compareTo(ta);
    });
    return list;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final sessions = _sessions();
    final entries = _entries();
    final hasAny = sessions.isNotEmpty || _groups.isNotEmpty;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLowest,
      appBar: wechatGlassAppBar(
        context,
        title: Row(
          children: [
            Icon(Icons.chat_bubble, size: 20),
            const SizedBox(width: 6),
            Text(l10n.chatOf(character.name)),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.group_add),
            tooltip: l10n.createGroup,
            onPressed: _createGroup,
          ),
        ],
      ),
      body: !hasAny
          // Aurora P3：空态 EmptyState 统一渲染
          ? ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                SizedBox(height: MediaQuery.of(context).size.height * 0.22),
                EmptyState(
                  icon: Icons.chat_bubble_outline_rounded,
                  title: l10n.noChatRecords,
                ),
              ],
            )
          : ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: entries.length,
              itemBuilder: (context, i) => _buildEntry(entries[i]),
            ),
    );
  }

  Widget _buildEntry(ChatEntry e) => e.isGroup ? _buildGroupEntry(e) : _buildDmEntry(e);

  Widget _buildDmEntry(ChatEntry e) {
    final l10n = AppLocalizations.of(context)!;
    final msgs = e.msgs!;
    final last = msgs.last;
    final other = e.other;
    return FutureBuilder<int>(
      future: _unreadCount(msgs, e.otherId!),
      builder: (context, snap) {
        final unread = snap.data ?? 0;
        // Aurora P3：ListTile → AuroraCard 行（内置按压，列表内不包模糊）
        return Padding(
          padding: const EdgeInsets.only(left: 12, right: 12, bottom: 8),
          child: AuroraCard(
            onTap: () async {
              await Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => WechatChatScreen(
                    self: character,
                    other: other ?? AICharacter(id: e.otherId!, name: e.otherName),
                    chats: msgs,
                  ),
                ),
              );
              if (mounted) setState(() {});
            },
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                AIAvatar(
                  name: e.otherName,
                  size: 44,
                  imageUrl: other?.avatarUrl,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(e.otherName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(last.content,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                              fontSize: 13,
                              color: Theme.of(context).colorScheme.onSurfaceVariant)),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      wechatTime(last.createdAt, l10n),
                      style: const TextStyle(
                          fontSize: 11, color: IosCardColors.subtitle),
                    ),
                    if (unread > 0) ...[
                      const SizedBox(height: 4),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.error,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Text(
                          '$unread',
                          style: const TextStyle(fontSize: 10, color: Colors.white),
                        ),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _buildGroupEntry(ChatEntry e) {
    final l10n = AppLocalizations.of(context)!;
    final g = e.group!;
    final gid = g['id'] as int;
    final name = g['name'] as String? ?? l10n.groupTitle;
    final lastMsg = e.lastMsg;
    final lastText = (lastMsg != null && lastMsg.isNotEmpty)
        ? '${lastMsg['sender_name'] ?? ''}: ${lastMsg['content'] ?? ''}'
        : (g['members'] as List? ?? [])
            .map((m) => (m as Map)['name']?.toString() ?? '')
            .join('、');
    final unread = _groupUnread[gid] ?? 0;
    // Aurora P3：ListTile → AuroraCard 行（长按删群包外层 GestureDetector）
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 8),
      child: GestureDetector(
        onLongPress: () => _deleteGroup(gid),
        child: AuroraCard(
          onTap: () async {
            await Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => ChatGroupChatScreen(
                  groupId: gid,
                  groupName: name,
                  viewCharacter: character, // 谁的入口进入，右侧就是谁
                ),
              ),
            );
            if (mounted) {
              setState(() {});
              await _loadGroups();
            }
          },
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(Icons.groups, color: Colors.white, size: 24),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 2),
                    Text(lastText,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            fontSize: 13,
                            color: Theme.of(context).colorScheme.onSurfaceVariant)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  if (e.lastTime != null)
                    Text(
                      wechatTime(e.lastTime!, l10n),
                      style: const TextStyle(
                          fontSize: 11, color: IosCardColors.subtitle),
                    ),
                  if (unread > 0) ...[
                    const SizedBox(height: 4),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(
                        color: Theme.of(context).colorScheme.error,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        '$unread',
                        style: const TextStyle(fontSize: 10, color: Colors.white),
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 畅聊主页统一会话项（私信 / 家庭群聊）
class ChatEntry {
  ChatEntry.dm({
    required this.otherId,
    required this.msgs,
    this.other,
    required this.otherName,
  })  : isGroup = false,
        group = null,
        lastMsg = null;

  ChatEntry.group({
    required this.group,
    this.lastMsg,
  })  : isGroup = true,
        otherId = null,
        msgs = null,
        other = null,
        otherName = '';

  final bool isGroup;
  final int? otherId;
  final List<AIChat>? msgs;
  final AICharacter? other;
  final String otherName;
  final Map<String, dynamic>? group;
  final Map<String, dynamic>? lastMsg;

  DateTime? get lastTime {
    if (isGroup) {
      final m = lastMsg;
      if (m == null || m.isEmpty) return null;
      try {
        return DateTime.parse(formatBeijingTime(m['created_at'] as String? ?? ''));
      } catch (_) {
        return null;
      }
    }
    final list = msgs;
    if (list == null || list.isEmpty) return null;
    return list.last.createdAt;
  }
}

/// 内置畅聊聊天窗口（该角色视角，只读）
