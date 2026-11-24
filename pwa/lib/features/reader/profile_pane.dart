// 只读工作台（M1a-2）· 角色详情面板：GET /api/v1/characters/{id} 的展示白名单字段。
// 纪律：system_prompt / LLM 配置等内部字段不进 DTO 也不展示；头像不加载图片（仅占位）；
// 空值字段直接不显示，不编造占位文本；角色名/简介等用户内容原文展示、不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'agent_mind_page.dart';
import 'character_settings_page.dart';
import 'character_world_page.dart';
import 'char_diary_pane.dart';
import 'memories_pane.dart';
import 'life_pane.dart';
import 'character_status_page.dart';
import 'avatar_picker.dart';
import 'timeline_page.dart';
import 'phone_shell_page.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

class ProfilePane extends StatefulWidget {
  const ProfilePane({
    super.key,
    required this.character,
    required this.client,
    required this.editor,
    required this.rs,
    required this.cs,
    this.onEdit,
    this.editLabel = 'Edit profile',
  });

  final Character character;
  final AmbApiClient client;
  final EditorApi editor;
  final ReaderStrings rs;
  final CoreStrings cs;

  /// 编辑入口回调（null ⇒ 不显示入口；权限由服务端判定，不在此按 owner 猜）。
  final VoidCallback? onEdit;
  final String editLabel;

  @override
  State<ProfilePane> createState() => _ProfilePaneState();
}

class _ProfilePaneState extends State<ProfilePane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<ReaderCharacter> _loader =
      ReaderLoader<ReaderCharacter>();
  ReaderRelationship? _relationship;
  List<ReaderWorldFact> _facts = const [];
  List<ReaderLorebookEntry> _lorebook = const [];
  Map<String, dynamic>? _agentMind;
  Map<String, dynamic>? _stateHistory;
  bool _uploadingAvatar = false;

  @override
  void initState() {
    super.initState();
    _reload();
    _loadExtras();
  }

  /// 附区逐项容错（2026-11-24）：任一子请求失败只让该块为空，不连带清空其它块。
  /// （历史 bug：lorebook/agent-mind 路径曾丢角色 id ⇒ 一个 404 把关系/世界事实/
  ///  内心摘要/状态历史整批吞掉。）
  Future<void> _loadExtras() async {
    Future<T?> guard<T>(Future<T> Function() call) async {
      try {
        return await call();
      } catch (_) {
        return null;
      }
    }

    final results = await Future.wait([
      guard(() => _api.getRelationship(widget.character.id)),
      guard(() => _api.getWorldFacts(widget.character.id)),
      guard(() => _api.getLorebook(widget.character.id)),
      guard(() => _api.getAgentMind(widget.character.id)),
      guard(() => _api.getStateHistory(widget.character.id)),
    ]);
    if (!mounted) return;
    setState(() {
      _relationship = results[0] as ReaderRelationship?;
      _facts = (results[1] as List<ReaderWorldFact>?) ?? const [];
      _lorebook = (results[2] as List<ReaderLorebookEntry>?) ?? const [];
      _agentMind = (results[3] as Map<String, dynamic>?);
      _stateHistory = (results[4] as Map<String, dynamic>?);
    });
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load(
      (ct) => _api.getCharacter(widget.character.id, cancelToken: ct),
      isEmpty: (_) => false,
    );
  }

  Widget _row(BuildContext context, String label, String value) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: theme.textTheme.labelSmall),
        const SizedBox(height: 2),
        SelectableText(value, style: theme.textTheme.bodyMedium),
      ]),
    );
  }

  void _openPhone() {
    Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => PhoneShellPage(
                client: widget.client,
                characterId: widget.character.id,
                characterName: widget.character.name,
                rs: widget.rs)));
  }

  void _openTimeline() {
    Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => TimelinePage(
                client: widget.client,
                characterId: widget.character.id,
                rs: widget.rs)));
  }

  void _openSettings() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CharacterSettingsPage(
            client: widget.client,
            editor: widget.editor,
            characterId: widget.character.id,
            characterName: widget.character.name,
            rs: widget.rs)));
  }

  void _openAgentMind() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => AgentMindPage(
            client: widget.client,
            characterId: widget.character.id,
            characterName: widget.character.name,
            rs: widget.rs)));
  }

  String? _avatarSrc(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
    var base = widget.client.baseUrl;
    if (base.endsWith('/')) base = base.substring(0, base.length - 1);
    return raw.startsWith('/') ? '$base$raw' : '$base/$raw';
  }

  Future<void> _changeAvatar() async {
    if (_uploadingAvatar) return;
    final bytes = await pickAvatarBytes();
    if (bytes == null || bytes.isEmpty) return;
    setState(() => _uploadingAvatar = true);
    try {
      final api = AvatarApi(widget.client);
      final url = await api.upload(bytes, filename: 'avatar.png');
      await api.setAvatar(widget.character.id, url);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('avatarUpdated'))));
      _reload();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('avatarFailed'))));
    } finally {
      if (mounted) setState(() => _uploadingAvatar = false);
    }
  }

  void _openStatus() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CharacterStatusPage(
            client: widget.client,
            characterId: widget.character.id,
            characterName: widget.character.name,
            rs: widget.rs,
            cs: widget.cs)));
  }

  void _openLife() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => Scaffold(
              appBar: AppBar(title: Text(widget.rs.t('life'))),
              body: LifePane(
                  character: widget.character,
                  client: widget.client,
                  rs: widget.rs,
                  cs: widget.cs),
            )));
  }

  void _openDiary() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => Scaffold(
              appBar: AppBar(title: Text(widget.rs.t('modCharDiary'))),
              body: CharDiaryPane(
                  character: widget.character,
                  client: widget.client,
                  rs: widget.rs,
                  cs: widget.cs),
            )));
  }

  void _openMemoryBook() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => Scaffold(
              appBar: AppBar(title: Text(widget.rs.t('memoryBook'))),
              body: MemoriesPane(
                  character: widget.character,
                  client: widget.client,
                  editor: widget.editor,
                  rs: widget.rs,
                  cs: widget.cs),
            )));
  }

  void _openWorld() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CharacterWorldPage(
            client: widget.client,
            editor: widget.editor,
            characterId: widget.character.id,
            characterName: widget.character.name,
            rs: widget.rs)));
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return ReaderAsyncView<ReaderCharacter>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (c) {
        final rows = <(String, String)>[
          (rs.t('name'), c.name),
          if (c.bio?.isNotEmpty ?? false) (rs.t('bio'), c.bio!),
          if (c.selfStatement?.isNotEmpty ?? false)
            (rs.t('selfStatement'), c.selfStatement!),
          if (c.personality?.isNotEmpty ?? false)
            (rs.t('personality'), c.personality!),
          if (c.relationshipSummary?.isNotEmpty ?? false)
            (rs.t('relationship'), c.relationshipSummary!),
          if (c.currentStatus?.isNotEmpty ?? false)
            (rs.t('currentStatus'), c.currentStatus!),
          if (c.gender?.isNotEmpty ?? false) (rs.t('gender'), c.gender!),
          if (c.birthday?.isNotEmpty ?? false) (rs.t('birthday'), c.birthday!),
          if (c.height != null) (rs.t('height'), '${c.height}'),
          if (c.weight != null) (rs.t('weight'), '${c.weight}'),
          if (c.appearance?.isNotEmpty ?? false)
            (rs.t('appearance'), c.appearance!),
          if (c.talkativeness != null)
            (rs.t('talkativeness'), '${c.talkativeness}'),
          if (c.timezoneOffset != null)
            (rs.t('timezone'), '${c.timezoneOffset}'),
          (rs.t('createdAt'), c.createdAt),
        ];
        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 6,
              alignment: WrapAlignment.end,
              children: [
                if (widget.onEdit != null)
                  OutlinedButton.icon(
                    onPressed: widget.onEdit,
                    icon: const Icon(Icons.edit, size: 18),
                    label: Text(widget.editLabel),
                  ),
                OutlinedButton.icon(
                  onPressed: _openSettings,
                  icon: const Icon(Icons.settings_outlined, size: 18),
                  label: Text(rs.t('openSettings')),
                ),
                OutlinedButton.icon(
                  onPressed: _openAgentMind,
                  icon: const Icon(Icons.psychology_outlined, size: 18),
                  label: Text(rs.t('openAgentMind')),
                ),
              ],
            ),
            Row(children: [
              CircleAvatar(
                radius: 32,
                backgroundColor:
                    Theme.of(context).colorScheme.surfaceContainerHighest,
                backgroundImage: _avatarSrc(c.avatarUrl) == null
                    ? null
                    : NetworkImage(_avatarSrc(c.avatarUrl)!),
                child: _avatarSrc(c.avatarUrl) != null
                    ? null
                    : Text(c.name.isEmpty ? '?' : c.name.characters.first),
              ),
              const SizedBox(width: 12),
              TextButton.icon(
                onPressed: _uploadingAvatar ? null : _changeAvatar,
                icon: const Icon(Icons.photo_camera_outlined, size: 18),
                label: Text(widget.rs.t('changeAvatar')),
              ),
              if (_uploadingAvatar)
                const Padding(
                  padding: EdgeInsets.all(8),
                  child: SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2)),
                ),
            ]),
            const SizedBox(height: 12),
            for (final (label, value) in rows) _row(context, label, value),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: _openTimeline,
              icon: const Icon(Icons.timeline, size: 18),
              label: Text(rs.t('timeline')),
            ),
            const SizedBox(width: 8),
            OutlinedButton.icon(
              onPressed: _openPhone,
              icon: const Icon(Icons.smartphone, size: 18),
              label: Text(rs.t('phoneTitle')),
            ),
            const Divider(height: 28),
            Text(rs.t('taDaily'), style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 6),
            Card(
              margin: EdgeInsets.zero,
              child: Column(children: [
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.self_improvement_outlined, size: 20),
                  title: Text(rs.t('life')),
                  subtitle: Text(rs.t('taLifeHint')),
                  trailing: const Icon(Icons.chevron_right, size: 18),
                  onTap: _openLife,
                ),
                const Divider(height: 1),
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.book_outlined, size: 20),
                  title: Text(rs.t('modCharDiary')),
                  subtitle: Text(rs.t('taDiaryHint')),
                  trailing: const Icon(Icons.chevron_right, size: 18),
                  onTap: _openDiary,
                ),
                const Divider(height: 1),
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.auto_stories_outlined, size: 20),
                  title: Text(rs.t('memoryBook')),
                  subtitle: Text(rs.t('memoryBookHint')),
                  trailing: const Icon(Icons.chevron_right, size: 18),
                  onTap: _openMemoryBook,
                ),
              ]),
            ),
            if (_relationship != null) ...[
              const Divider(height: 28),
              Text(rs.t('relationMeta'),
                  style: Theme.of(context).textTheme.titleSmall),
              _row(context, rs.t('relationType'),
                  _relationship!.relationType),
              if (_relationship!.isPartner)
                _row(context, rs.t('relationType'), rs.t('partner')),
            ],
            const Divider(height: 28),
            Card(
              margin: EdgeInsets.zero,
              child: ListTile(
                dense: true,
                leading: const Icon(Icons.public, size: 20),
                title: Text(rs.t('worldSettings')),
                subtitle: Text(
                  '${rs.t('lorebook')} ${_lorebook.length} · '
                  '${rs.t('worldFacts')} ${_facts.length}',
                ),
                trailing: const Icon(Icons.chevron_right, size: 18),
                onTap: _openWorld,
              ),
            ),
            if ((_stateHistory?['points'] as List?)?.isNotEmpty ?? false) ...[
              const Divider(height: 28),
              Row(children: [
                Expanded(
                  child: Text(rs.t('stateHistory'),
                      style: Theme.of(context).textTheme.titleSmall),
                ),
                TextButton(
                  onPressed: _openStatus,
                  child: Text(rs.t('statusView')),
                ),
              ]),
              Builder(builder: (context) {
                final p = (_stateHistory!['points'] as List).last as Map;
                final dims = {
                  'mood': 'mood',
                  'body_temp': 'body_temp',
                  'desire': 'desire',
                  'possessiveness': 'possessiveness',
                  'fatigue': 'fatigue',
                  'sensitivity': 'sensitivity',
                  'comfort': 'comfort',
                  'anger': 'anger',
                };
                return Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    for (final e in dims.entries)
                      if (p[e.key] != null)
                        Chip(
                          label: Text('${e.value}: ${p[e.key]}'),
                          visualDensity: VisualDensity.compact,
                        ),
                  ],
                );
              }),
            ],
            if (_agentMind != null) ...[
              const Divider(height: 28),
              Row(children: [
                Expanded(
                  child: Text(rs.t('agentMind'),
                      style: Theme.of(context).textTheme.titleSmall),
                ),
                TextButton(
                  onPressed: _openAgentMind,
                  child: Text(rs.t('openAgentMind')),
                ),
              ]),
              if ((_agentMind!['reflection'] as Map?)?['content'] is String)
                _row(context, rs.t('reflection'),
                    (_agentMind!['reflection'] as Map)['content'] as String),
              if ((_agentMind!['tasks'] as List?)?.isNotEmpty ?? false)
                _row(context, rs.t('tasks'),
                    '${(_agentMind!['tasks'] as List).length}'),
              if ((_agentMind!['tool_logs'] as List?)?.isNotEmpty ?? false)
                for (final log in (_agentMind!['tool_logs'] as List).take(5))
                  _row(
                    context,
                    '${rs.t('toolLog')} · ${(log as Map)['trigger'] ?? ''}',
                    '${log['status'] ?? ''} · ${log['route'] ?? ''}',
                  ),
            ],
          ],
        );
      },
    );
  }
}
