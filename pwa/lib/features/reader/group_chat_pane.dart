import 'dart:async';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 家庭群聊（核心范围外第一块）：群列表 / 文字消息 / 同步多角色回复 / 20s 增量轮询。
class GroupChatPane extends StatefulWidget {
  const GroupChatPane({
    super.key,
    required this.client,
    required this.characters,
    required this.filePicker,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final List<Character> characters;
  final TextFilePicker filePicker;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<GroupChatPane> createState() => _GroupChatPaneState();
}

class _GroupChatPaneState extends State<GroupChatPane> {
  late final GroupChatApi _api = GroupChatApi(widget.client);
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();

  List<ChatGroupSummary> _groups = const [];
  List<GroupChatMessage> _messages = const [];
  int? _selectedGroupId;
  bool _loadingGroups = true;
  bool _loadingMessages = false;
  bool _sending = false;
  bool _uploadingFile = false;
  String _error = '';
  CancelToken? _inflight;
  Timer? _poll;
  int _lastMentionId = 0;

  ChatGroupSummary? get _selected {
    for (final g in _groups) {
      if (g.id == _selectedGroupId) return g;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _loadGroups();
    _poll = Timer.periodic(const Duration(seconds: 20), (_) => _pollNew());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _inflight?.cancel('dispose');
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadGroups() async {
    setState(() {
      _loadingGroups = true;
      _error = '';
    });
    final ct = CancelToken();
    _inflight = ct;
    try {
      final groups = await _api.listGroups(cancelToken: ct);
      if (!mounted) return;
      setState(() {
        _groups = groups;
        _loadingGroups = false;
        if (_selectedGroupId == null && groups.isNotEmpty) {
          _selectedGroupId = groups.first.id;
        }
      });
      if (_selectedGroupId != null) await _loadMessages(_selectedGroupId!);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingGroups = false;
        _error = '${widget.rs.t('groupLoadFailed')}：$e';
      });
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
    }
  }

  Future<void> _loadMessages(int groupId) async {
    _inflight?.cancel('switch-group');
    final ct = CancelToken();
    _inflight = ct;
    setState(() {
      _loadingMessages = true;
      _error = '';
    });
    try {
      final items = await _api.listMessages(groupId, limit: 100, cancelToken: ct);
      if (!mounted || _selectedGroupId != groupId) return;
      setState(() {
        _messages = items;
        _loadingMessages = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted || _selectedGroupId != groupId) return;
      setState(() {
        _loadingMessages = false;
        _error = '${widget.rs.t('groupLoadFailed')}：$e';
      });
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
    }
  }

  void _selectGroup(int id) {
    if (id == _selectedGroupId) return;
    setState(() {
      _selectedGroupId = id;
      _messages = const [];
    });
    _loadMessages(id);
  }

  Future<void> _pollNew() async {
    final gid = _selectedGroupId;
    if (!mounted || gid == null || _sending || _loadingMessages) return;
    try {
      final items = await _api.listMessages(gid, limit: 100);
      if (!mounted || _selectedGroupId != gid) return;
      final lastId = _messages.isEmpty ? 0 : _messages.last.id;
      final fresh = items.where((m) => m.id > lastId).toList();
      if (fresh.isEmpty) return;
      setState(() => _messages = [..._messages, ...fresh]);
      _scrollToBottom();
    } catch (_) {
      // 轮询失败静默，下轮再试；不把旧消息替换成空。
    }
    await _pollMentions();
  }

  Future<void> _pollMentions() async {
    try {
      final mentions = await _api.listMentions(afterId: _lastMentionId);
      if (!mounted || mentions.isEmpty) return;
      final latest = mentions.last;
      _lastMentionId = latest.id;
      _showMention(latest);
    } catch (_) {
      // 提醒失败静默，下轮再试。
    }
  }

  void _showMention(GroupMention m) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(widget.rs.t('mentionNotice')
          .replaceAll('{name}', m.senderName)
          .replaceAll('{group}', m.groupName)),
    ));
  }

  Future<void> _pickMention() async {
    final g = _selected;
    if (g == null || g.members.isEmpty) return;
    final picked = await showModalBottomSheet<GroupMember>(
      context: context,
      builder: (ctx) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          for (final m in g.members)
            ListTile(
              leading: CircleAvatar(
                radius: 15,
                child: Text(m.name.isEmpty ? '?' : m.name.characters.first),
              ),
              title: Text(m.name),
              onTap: () => Navigator.pop(ctx, m),
            ),
        ]),
      ),
    );
    if (picked == null || picked.name.isEmpty) return;
    final text = _input.text;
    final pos = _input.selection.baseOffset;
    final at = (pos < 0 || pos > text.length) ? text.length : pos;
    final insertion = '@${picked.name} ';
    final next = text.substring(0, at) + insertion + text.substring(at);
    _input.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: at + insertion.length),
    );
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final gid = _selectedGroupId;
    final text = _input.text.trim();
    if (gid == null || text.isEmpty || _sending) return;
    setState(() {
      _sending = true;
      _error = '';
    });
    _input.clear();
    try {
      final result = await _api.sendMessage(gid, text);
      if (!mounted || _selectedGroupId != gid) return;
      final mentioned = result.replies.where((m) => m.notifyUser).toList();
      if (mentioned.isNotEmpty) {
        _lastMentionId = mentioned.map((m) => m.id).fold(_lastMentionId, (a, b) => a > b ? a : b);
        _showMention(GroupMention(
          id: mentioned.last.id,
          groupId: gid,
          groupName: _selected?.name ?? '',
          senderName: mentioned.last.senderName,
          content: mentioned.last.content,
        ));
      }
      setState(() {
        _messages = [..._messages, result.userMessage, ...result.replies];
        _sending = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _sending = false;
        _error = '${widget.rs.t('groupSendFailed')}：$e';
      });
    }
  }

  Future<void> _pickGroupFile() async {
    final gid = _selectedGroupId;
    if (gid == null || _uploadingFile || _sending) return;
    final picked = await widget.filePicker.pick();
    if (picked == null || !mounted) return;
    if (picked.reject == TextFileRejectReason.tooLarge) {
      setState(() => _error = widget.rs.t('groupFileTooLarge'));
      return;
    }
    if (picked.reject != null || !picked.accepted) {
      setState(() => _error = widget.rs.t('groupFileRejected'));
      return;
    }
    setState(() {
      _uploadingFile = true;
      _error = '';
    });
    try {
      final msg = await _api.uploadFile(gid, picked, lang: widget.rs.lang);
      if (!mounted || _selectedGroupId != gid) return;
      setState(() {
        _messages = [..._messages, msg];
        _uploadingFile = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _uploadingFile = false;
        _error = '${widget.rs.t('groupFileFailed')}：$e';
      });
    }
  }

  Future<void> _createGroup() async {
    final ctrl = TextEditingController();
    final selected = <int>{};
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: Text(widget.rs.t('createGroup')),
          content: SizedBox(
            width: 430,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(
                controller: ctrl,
                decoration: InputDecoration(hintText: widget.rs.t('groupName')),
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: Text(widget.rs.t('groupMembers'),
                    style: Theme.of(ctx).textTheme.bodySmall),
              ),
              SizedBox(
                height: 220,
                child: ListView(
                  children: [
                    for (final c in widget.characters)
                      CheckboxListTile(
                        dense: true,
                        value: selected.contains(c.id),
                        title: Text(c.name),
                        onChanged: (v) => setDlg(() {
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
              if (selected.length < 2)
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text(widget.rs.t('groupNeedMembers'),
                      style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
                ),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: Text(widget.rs.t('cancel'))),
            FilledButton(
              onPressed: selected.length < 2
                  ? null
                  : () async {
                      final name = ctrl.text.trim().isEmpty
                          ? widget.rs.t('groupDefaultName')
                          : ctrl.text.trim();
                      try {
                        await _api.createGroup(name, selected.toList());
                        if (ctx.mounted) Navigator.pop(ctx, true);
                      } catch (e) {
                        if (!ctx.mounted) return;
                        ScaffoldMessenger.of(ctx).showSnackBar(
                            SnackBar(content: Text('${widget.rs.t('groupCreateFailed')}：$e')));
                      }
                    },
              child: Text(widget.rs.t('create')),
            ),
          ],
        ),
      ),
    );
    if (created == true && mounted) {
      await _loadGroups();
    }
  }

  Future<void> _manageGroup() async {
    final g = _selected;
    if (g == null) return;
    var members = List<GroupMember>.of(g.members);
    var changed = false;
    var deleted = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) {
          final memberIds = members.map((m) => m.id).toSet();
          final available = widget.characters
              .where((c) => !memberIds.contains(c.id))
              .toList();
          return AlertDialog(
            title: Text('${widget.rs.t('manageGroup')} · ${g.name}'),
            content: SizedBox(
              width: 440,
              height: 400,
              child: ListView(children: [
                for (final m in members)
                  ListTile(
                    dense: true,
                    leading: CircleAvatar(
                      radius: 15,
                      child: Text(m.name.isEmpty ? '?' : m.name.characters.first),
                    ),
                    title: Text(m.name),
                    subtitle: Text(m.muted
                        ? widget.rs.t('muted')
                        : widget.rs.t('unmuted')),
                    trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                      Switch(
                        value: m.muted,
                        onChanged: (v) async {
                          try {
                            await _api.setMemberMuted(g.id, m.id, v);
                            setDlg(() {
                              members = [
                                for (final x in members)
                                  if (x.id == m.id)
                                    GroupMember(id: x.id, name: x.name, muted: v)
                                  else
                                    x,
                              ];
                              changed = true;
                            });
                          } catch (e) {
                            if (ctx.mounted) {
                              ScaffoldMessenger.of(ctx).showSnackBar(
                                  SnackBar(content: Text('${widget.rs.t('groupManageFailed')}：$e')));
                            }
                          }
                        },
                      ),
                      IconButton(
                        tooltip: widget.rs.t('removeMember'),
                        onPressed: members.length <= 2
                            ? null
                            : () async {
                                try {
                                  await _api.removeMember(g.id, m.id);
                                  setDlg(() {
                                    members = members
                                        .where((x) => x.id != m.id)
                                        .toList();
                                    changed = true;
                                  });
                                } catch (e) {
                                  if (ctx.mounted) {
                                    ScaffoldMessenger.of(ctx).showSnackBar(
                                        SnackBar(content: Text('${widget.rs.t('groupManageFailed')}：$e')));
                                  }
                                }
                              },
                        icon: const Icon(Icons.remove_circle_outline, size: 18),
                      ),
                    ]),
                  ),
                const Divider(),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                  child: Text(widget.rs.t('addMember'),
                      style: Theme.of(ctx).textTheme.bodySmall),
                ),
                if (available.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Text(widget.rs.t('groupNoMoreMembers'),
                        style: Theme.of(ctx).textTheme.bodySmall),
                  ),
                for (final c in available)
                  ListTile(
                    dense: true,
                    leading: CircleAvatar(
                      radius: 15,
                      child: Text(c.name.isEmpty ? '?' : c.name.characters.first),
                    ),
                    title: Text(c.name),
                    trailing: IconButton(
                      tooltip: widget.rs.t('addMember'),
                      onPressed: () async {
                        try {
                          await _api.addMembers(g.id, [c.id]);
                          setDlg(() {
                            members = [
                              ...members,
                              GroupMember(id: c.id, name: c.name),
                            ];
                            changed = true;
                          });
                        } catch (e) {
                          if (ctx.mounted) {
                            ScaffoldMessenger.of(ctx).showSnackBar(
                                SnackBar(content: Text('${widget.rs.t('groupManageFailed')}：$e')));
                          }
                        }
                      },
                      icon: const Icon(Icons.add_circle_outline, size: 18),
                    ),
                  ),
                const Divider(),
                ListTile(
                  leading: Icon(Icons.delete_outline,
                      color: Theme.of(ctx).colorScheme.error),
                  title: Text(widget.rs.t('deleteGroup'),
                      style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
                  onTap: () async {
                    final sure = await showDialog<bool>(
                      context: ctx,
                      builder: (c2) => AlertDialog(
                        title: Text(widget.rs.t('deleteGroup')),
                        content: Text(widget.rs.t('deleteGroupBody')),
                        actions: [
                          TextButton(
                              onPressed: () => Navigator.pop(c2, false),
                              child: Text(widget.rs.t('cancel'))),
                          FilledButton(
                              onPressed: () => Navigator.pop(c2, true),
                              child: Text(widget.rs.t('delete'))),
                        ],
                      ),
                    );
                    if (sure != true) return;
                    try {
                      await _api.deleteGroup(g.id);
                      deleted = true;
                      changed = true;
                      if (ctx.mounted) Navigator.pop(ctx, true);
                    } catch (e) {
                      if (ctx.mounted) {
                        ScaffoldMessenger.of(ctx).showSnackBar(
                            SnackBar(content: Text('${widget.rs.t('groupManageFailed')}：$e')));
                      }
                    }
                  },
                ),
              ]),
            ),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx, changed),
                  child: Text(widget.rs.t('done'))),
            ],
          );
        },
      ),
    );
    if (!mounted) return;
    if (deleted) {
      setState(() {
        _selectedGroupId = null;
        _messages = const [];
      });
    }
    if (ok == true || changed) {
      await _loadGroups();
    }
  }

  String _size(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }

  String _time(String raw) {
    final dt = DateTime.tryParse(raw.endsWith('Z') ? raw : '${raw}Z');
    if (dt == null) return raw;
    final local = dt.toUtc().add(const Duration(hours: 8));
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(local.month)}-${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final rs = widget.rs;
    if (_loadingGroups) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_groups.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(rs.t('groupEmpty')),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _createGroup,
            icon: const Icon(Icons.add),
            label: Text(rs.t('createGroup')),
          ),
          if (_error.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(_error, style: TextStyle(color: theme.colorScheme.error)),
          ],
        ]),
      );
    }
    final wide = MediaQuery.sizeOf(context).width >= 760;
    final list = _groupList(theme);
    final chat = _chat(theme);
    return wide
        ? Row(children: [
            SizedBox(width: 236, child: list),
            const VerticalDivider(width: 1),
            Expanded(child: chat),
          ])
        : Column(children: [
            SizedBox(height: 52, child: list),
            const Divider(height: 1),
            Expanded(child: chat),
          ]);
  }

  Widget _groupList(ThemeData theme) {
    return ListView(padding: const EdgeInsets.symmetric(vertical: 8), children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 8, 6),
        child: Row(children: [
          Expanded(
            child: Text(widget.rs.t('groups'),
                style: theme.textTheme.titleSmall),
          ),
          IconButton(
            tooltip: widget.rs.t('createGroup'),
            onPressed: _createGroup,
            icon: const Icon(Icons.add, size: 18),
          ),
        ]),
      ),
      for (final g in _groups)
        ListTile(
          dense: true,
          selected: g.id == _selectedGroupId,
          leading: CircleAvatar(
            radius: 15,
            child: Text(g.name.isEmpty ? '群' : g.name.characters.first),
          ),
          title: Text(g.name.isEmpty ? widget.rs.t('groupDefaultName') : g.name,
              maxLines: 1, overflow: TextOverflow.ellipsis),
          subtitle: Text(
            g.members.map((m) => m.name).where((n) => n.isNotEmpty).join('、'),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          onTap: () => _selectGroup(g.id),
        ),
    ]);
  }

  Widget _chat(ThemeData theme) {
    final g = _selected;
    return Column(children: [
      Container(
        height: 48,
        padding: const EdgeInsets.symmetric(horizontal: 14),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: theme.dividerColor)),
        ),
        child: Row(children: [
          Expanded(
            child: Text(g?.name ?? widget.rs.t('groups'),
                style: theme.textTheme.titleSmall),
          ),
          if (_sending)
            Text(widget.rs.t('groupSending'),
                style: theme.textTheme.bodySmall),
          IconButton(
            tooltip: widget.rs.t('manageGroup'),
            onPressed: g == null ? null : _manageGroup,
            icon: const Icon(Icons.manage_accounts_outlined, size: 20),
          ),
        ]),
      ),
      if (_error.isNotEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Text(_error,
              style: TextStyle(color: theme.colorScheme.error, fontSize: 12)),
        ),
      Expanded(
        child: _loadingMessages
            ? const Center(child: CircularProgressIndicator())
            : _messages.isEmpty
                ? Center(child: Text(widget.rs.t('groupNoMessages')))
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.all(12),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _messageTile(theme, _messages[i]),
                  ),
      ),
      Padding(
        padding: const EdgeInsets.all(10),
        child: Row(children: [
          IconButton(
            tooltip: widget.rs.t('groupFile'),
            onPressed: _sending || _uploadingFile ? null : _pickGroupFile,
            icon: const Icon(Icons.attach_file, size: 20),
          ),
          IconButton(
            tooltip: widget.rs.t('mention'),
            onPressed: _sending ? null : _pickMention,
            icon: const Icon(Icons.alternate_email, size: 20),
          ),
          Expanded(
            child: TextField(
              controller: _input,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(hintText: widget.rs.t('groupHint')),
            ),
          ),
          const SizedBox(width: 8),
          FilledButton(
            onPressed: _sending ? null : _send,
            child: Text(_sending
                ? widget.rs.t('groupSending')
                : widget.cs.t('send')),
          ),
        ]),
      ),
    ]);
  }

  Widget _messageTile(ThemeData theme, GroupChatMessage m) {
    final mine = m.isUser;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.all(9),
        constraints: const BoxConstraints(maxWidth: 520),
        decoration: BoxDecoration(
          color: mine
              ? theme.colorScheme.primaryContainer
              : theme.cardTheme.color,
          border: Border.all(color: theme.dividerColor),
          borderRadius: BorderRadius.circular(7),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(mainAxisSize: MainAxisSize.min, children: [
            Text('${mine ? widget.cs.t('me') : m.senderName} · ${_time(m.createdAt)}',
                style: theme.textTheme.bodySmall),
            if (m.notifyUser) ...[
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(3),
                ),
                child: Text(widget.rs.t('youWereMentioned'),
                    style: theme.textTheme.bodySmall),
              ),
            ],
          ]),
          const SizedBox(height: 3),
          if (m.fileUrl != null && m.fileUrl!.isNotEmpty)
            Row(children: [
              const Icon(Icons.insert_drive_file_outlined, size: 18),
              const SizedBox(width: 6),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(m.fileName ?? m.content, maxLines: 1, overflow: TextOverflow.ellipsis),
                  if (m.fileSize != null)
                    Text(_size(m.fileSize!), style: theme.textTheme.bodySmall),
                ]),
              ),
            ])
          else
            Text(m.content),
        ]),
      ),
    );
  }
}
