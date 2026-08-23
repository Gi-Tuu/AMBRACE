import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/entrance_fade.dart';

/// 家庭群聊聊天页：消息列表 + 输入框；用户发言后单次生成多角色回应
class ChatGroupChatScreen extends StatefulWidget {
  const ChatGroupChatScreen({
    super.key,
    required this.groupId,
    required this.groupName,
    this.viewCharacter,
  });

  final int groupId;
  final String groupName;

  /// 视角角色：从该角色的小手机进入群聊时传入，其消息显示在右侧；null=用户视角
  final AICharacter? viewCharacter;

  @override
  State<ChatGroupChatScreen> createState() => _ChatGroupChatScreenState();
}

class _ChatGroupChatScreenState extends State<ChatGroupChatScreen> {
  final ApiClient _api = ApiClient();
  final _controller = TextEditingController();
  final _scroll = ScrollController();
  List<Map<String, dynamic>> _messages = [];
  bool _loading = true;
  bool _sending = false;
  Timer? _pollTimer;

  /// 上次构建时消息数（用于判定「新插入项」只对该项播放入场动画；-1 表示尚未构建过列表）。
  int _lastBuiltCount = -1;
  /// 消息列表是否已完成过一次「有内容」的构建（避免首帧装载历史消息时整屏播放入场动画）。
  bool _listBuilt = false;

  @override
  void initState() {
    super.initState();
    _load();
    // 轮询新消息：角色可能在群里主动冒泡（2026-08-15）
    _pollTimer = Timer.periodic(const Duration(seconds: 20), (_) => _pollNew());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _controller.dispose();
    _scroll.dispose();
    super.dispose();
  }

  /// 增量拉取新消息（只追加比本地最新的 id 更大的）
  Future<void> _pollNew() async {
    if (!mounted || _sending) return;
    try {
      final items = await _api.getChatGroupMessages(widget.groupId);
      if (!mounted || items.isEmpty) return;
      final lastId = _messages.isNotEmpty ? (_messages.last['id'] as num?)?.toInt() ?? 0 : 0;
      final fresh = items.where((m) => ((m['id'] as num?)?.toInt() ?? 0) > lastId).toList();
      if (fresh.isEmpty) return;
      setState(() => _messages.addAll(fresh));
      _scrollToBottom();
    } catch (_) {
      // 轮询失败静默，下轮重试
    }
  }

  Future<void> _load() async {
    try {
      final items = await _api.getChatGroupMessages(widget.groupId);
      if (mounted) {
        setState(() {
          _messages = items;
          _loading = false;
        });
        _scrollToBottom();
      }
      // 进入群聊即视为已读（红点依据 group_read_<id> 判断）
      final lastId =
          items.isNotEmpty ? ((items.last['id'] as num?)?.toInt() ?? 0) : 0;
      if (lastId > 0) {
        final sp = await SharedPreferences.getInstance();
        await sp.setInt('group_read_${widget.groupId}', lastId);
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final l10n = AppLocalizations.of(context)!;
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;
    _controller.clear();
    setState(() => _sending = true);
    try {
      final res = await _api.sendChatGroupMessage(widget.groupId, text);
      final userMsg = (res['user_message'] as Map?)?.cast<String, dynamic>();
      final replies = (res['replies'] as List? ?? []).cast<Map<String, dynamic>>();
      if (mounted) {
        setState(() {
          if (userMsg != null) _messages.add(userMsg);
          _messages.addAll(replies);
          _sending = false;
        });
        _scrollToBottom();
      }
    } catch (e) {
      if (mounted) {
        setState(() => _sending = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${l10n.sendFail}: $e')));
      }
    }
  }

  /// 时间显示：今天 HH:mm，更早 MM-dd HH:mm（created_at 为 UTC naive，转北京时间）
  String _fmtTime(String iso) {
    try {
      final t = DateTime.parse(formatBeijingTime(iso));
      final now = DateTime.now();
      final sameDay = t.year == now.year && t.month == now.month && t.day == now.day;
      return DateFormat(sameDay ? 'HH:mm' : 'MM-dd HH:mm').format(t);
    } catch (_) {
      return '';
    }
  }

  /// 群成员管理：查看/添加/移除角色
  Future<void> _manageMembers() async {
    await showDialog<void>(
      context: context,
      builder: (_) => _GroupMemberDialog(
        groupId: widget.groupId,
        groupName: widget.groupName,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    // 记录已构建消息数（供新插入项入场动画判定）；放在 post-frame 以读取本次构建后的最新长度，
    // 且仅在列表确实渲染出内容时更新，避免首帧装载历史消息时整屏播放入场动画。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_loading && _messages.isNotEmpty) {
        _listBuilt = true;
        _lastBuiltCount = _messages.length;
      }
    });
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.groupName),
        actions: [
          IconButton(
            tooltip: l10n.groupMembers,
            icon: const Icon(Icons.people_outline),
            onPressed: _manageMembers,
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _messages.isEmpty
                    ? Center(child: Text(l10n.groupChatEmpty, style: const TextStyle(color: Colors.grey)))

                    : ListView.builder(
                        controller: _scroll,
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        itemCount: _messages.length,
                        itemBuilder: (context, i) => EntranceFade(
                          animate: _listBuilt && i >= _lastBuiltCount,
                          child: _bubble(_messages[i], scheme),
                        ),
                      ),
          ),
          if (_sending)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 2)),
                  const SizedBox(width: 8),
                  Text(l10n.groupReplying, style: TextStyle(fontSize: 12, color: Colors.grey)),
                ],
              ),
            ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _send(),
                      decoration: InputDecoration(
                        hintText: l10n.groupInputHint,
                        isDense: true,
                        filled: true,
                        fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(20),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: _sending ? null : _send,
                    icon: const Icon(Icons.send, size: 20),
                    tooltip: l10n.sendChat,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 视角判定：谁的入口进入，右侧就是谁（角色视角=入口角色消息在右；用户视角=用户消息在右）
  bool _isMine(Map<String, dynamic> m) {
    final vc = widget.viewCharacter;
    if (vc != null) {
      return m['sender_type'] == 'ai' &&
          ((m['character_id'] as num?)?.toInt() ?? 0) == vc.id;
    }
    return m['sender_type'] == 'user';
  }

  Widget _aiAvatar(Map<String, dynamic> m, String name) {
    final l10n = AppLocalizations.of(context)!;
    final url = m['sender_avatar'] as String? ?? '';
    final display = name.isEmpty ? l10n.roleFallback : name;
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: AIAvatar(name: display, size: 34, imageUrl: url.isEmpty ? null : url),
    );
  }

  /// 右侧消息头像（气泡外侧最右）：角色视角=入口角色头像；用户视角=用户头像
  Widget _mineAvatar(ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final vc = widget.viewCharacter;
    if (vc != null) {
      return Padding(
        padding: const EdgeInsets.only(left: 6),
        child: AIAvatar(
          name: vc.name,
          size: 34,
          imageUrl: (vc.avatarUrl ?? '').isEmpty ? null : vc.avatarUrl,
        ),
      );
    }
    final settings = context.read<SettingsProvider>();
    final av = settings.avatarUrl;
    final nick = settings.nickname.isEmpty ? l10n.me : settings.nickname;
    return Padding(
      padding: const EdgeInsets.only(left: 6),
      child: AIAvatar(name: nick, size: 34, imageUrl: av.isEmpty ? null : av),
    );
  }

  Widget _bubble(Map<String, dynamic> m, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final mine = _isMine(m);
    final isUser = m['sender_type'] == 'user';
    final name = (m['sender_name'] as String? ?? '').isNotEmpty
        ? (m['sender_name'] as String)
        : (isUser ? l10n.you : l10n.roleFallback);
    final content = m['content'] as String? ?? '';
    final time = _fmtTime(m['created_at'] as String? ?? '');

    final bubble = Container(
      constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.72),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: mine ? scheme.primary : scheme.surfaceContainerHighest.withValues(alpha: 0.6),
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(14),
          topRight: const Radius.circular(14),
          bottomLeft: Radius.circular(mine ? 14 : 4),
          bottomRight: Radius.circular(mine ? 4 : 14),
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            content,
            style: TextStyle(
              fontSize: 14,
              height: 1.35,
              color: mine ? scheme.onPrimary : scheme.onSurface,
            ),
          ),
          // 时间统一放气泡内部左下角（仿照私信 MessageBubble）
          if (time.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                time,
                style: TextStyle(
                  fontSize: 10,
                  color: (mine ? scheme.onPrimary : scheme.onSurface).withValues(alpha: 0.55),
                ),
              ),
            ),
        ],
      ),
    );

    final avatar = mine ? _mineAvatar(scheme) : _aiAvatar(m, name);

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
        // 微信群聊格式：头像顶部与名字/气泡顶部齐平
        crossAxisAlignment: CrossAxisAlignment.start,
        children: mine
            ? [
                // 右侧用户消息：气泡 + 头像，头像顶部与气泡顶部持平（不显示名字）
                // 注意：不用 Flexible 包气泡——mainAxisAlignment.end 下 Flexible 槽从屏幕左端铺起，短气泡会贴左；气泡 maxWidth 0.72 本身不会溢出
                bubble,
                avatar,
              ]
            : [
                // 左侧 AI 消息：头像顶部与名字顶部齐平，气泡在名字下方
                avatar,
                Flexible(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // 名字在气泡上方（字号 11 保持，去掉原 left:4 bottom:3 的 Padding）
                      if (m['sender_type'] == 'ai') ...[
                        Text(
                          name,
                          style: TextStyle(
                            fontSize: 11,
                            color: scheme.onSurface.withValues(alpha: 0.55),
                          ),
                        ),
                        const SizedBox(height: 4),
                      ],
                      bubble,
                    ],
                  ),
                ),
              ],
      ),
    );
  }
}


/// 群成员管理弹窗：成员列表 + 添加角色 + 移除角色
class _GroupMemberDialog extends StatefulWidget {
  const _GroupMemberDialog({required this.groupId, required this.groupName});

  final int groupId;
  final String groupName;

  @override
  State<_GroupMemberDialog> createState() => _GroupMemberDialogState();
}

class _GroupMemberDialogState extends State<_GroupMemberDialog> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _members = [];
  List<AICharacter> _allChars = [];
  bool _loading = true;
  bool _picking = false; // 是否处于添加成员选择模式
  final Set<int> _selected = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final groups = await _api.getChatGroups();
      Map<String, dynamic> group = const {};
      for (final g in groups) {
        if ((g['id'] as int?) == widget.groupId) {
          group = g;
          break;
        }
      }
      final members = (group['members'] as List? ?? []).cast<Map<String, dynamic>>();
      final chars = await _api.getCharacters();
      if (mounted) {
        setState(() {
          _members = members;
          _allChars = chars;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? _avatarOf(int cid) {
    for (final c in _allChars) {
      if (c.id == cid) return c.avatarUrl;
    }
    return null;
  }

  Future<void> _remove(Map<String, dynamic> m) async {
    final l10n = AppLocalizations.of(context)!;
    final cid = (m['id'] as num?)?.toInt() ?? 0;
    try {
      await _api.removeChatGroupMember(widget.groupId, cid);
      if (mounted) {
        setState(() => _members.removeWhere((x) => (x['id'] as num?)?.toInt() == cid));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${l10n.groupRemoveFail}: $e')));
      }
    }
  }

  Future<void> _add(List<int> ids) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await _api.addChatGroupMembers(widget.groupId, ids);
      if (mounted) {
        setState(() {
          _picking = false;
          _selected.clear();
        });
        await _load();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${l10n.groupAddFail}: $e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AlertDialog(
      title: Text('${widget.groupName} · ${l10n.groupMembers}'),
      content: SizedBox(
        width: 340,
        height: 360,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _picking
                ? _buildPicker()
                : _buildMemberList(),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.close)),
      ],
    );
  }

  Widget _buildMemberList() {
    final l10n = AppLocalizations.of(context)!;
    final memberIds = _members.map((m) => (m['id'] as num?)?.toInt() ?? 0).toSet();
    final canAdd = _allChars.any((c) => !memberIds.contains(c.id));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _members.isEmpty
              ? Center(child: Text(l10n.groupMemberEmpty))
              : ListView(
                  children: [
                    for (final m in _members)
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        leading: AIAvatar(
                          name: m['name']?.toString() ?? l10n.roleFallback,
                          size: 32,
                          imageUrl: _avatarOf((m['id'] as num?)?.toInt() ?? 0),
                        ),
                        title: Text(m['name']?.toString() ?? l10n.roleFallback,
                            style: const TextStyle(fontSize: 14)),
                        trailing: IconButton(
                          icon: const Icon(Icons.person_remove_outlined, size: 18),
                          tooltip: l10n.remove,
                          onPressed: _members.length <= 2 ? null : () => _remove(m),
                        ),
                      ),
                  ],
                ),
        ),
        const SizedBox(height: 8),
        if (canAdd)
          FilledButton.tonalIcon(
            onPressed: () => setState(() => _picking = true),
            icon: const Icon(Icons.person_add_alt, size: 18),
            label: Text(l10n.addMember),
          ),
      ],
    );
  }

  Widget _buildPicker() {
    final l10n = AppLocalizations.of(context)!;
    final memberIds = _members.map((m) => (m['id'] as num?)?.toInt() ?? 0).toSet();
    final candidates = _allChars.where((c) => !memberIds.contains(c.id)).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(l10n.selectMembersHint, style: const TextStyle(fontSize: 13)),
        const SizedBox(height: 4),
        Expanded(
          child: candidates.isEmpty
              ? Center(child: Text(l10n.noAddableChars))
              : ListView(
                  children: [
                    for (final c in candidates)
                      CheckboxListTile(
                        dense: true,
                        title: Text(c.name, style: const TextStyle(fontSize: 14)),
                        value: _selected.contains(c.id),
                        onChanged: (v) => setState(() {
                          if (v == true) {
                            _selected.add(c.id);
                          } else {
                            _selected.remove(c.id);
                          }
                        }),
                      ),
                  ],
                ),
        ),
        const SizedBox(height: 8),
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            TextButton(
              onPressed: () => setState(() {
                _picking = false;
                _selected.clear();
              }),
              child: Text(l10n.back),
            ),
            FilledButton(
              onPressed: _selected.isEmpty ? null : () => _add(_selected.toList()),
              child: Text(l10n.add),
            ),
          ],
        ),
      ],
    );
  }
}
