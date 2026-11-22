// 只读工作台（M1a-2）· 角色详情面板：GET /api/v1/characters/{id} 的展示白名单字段。
// 纪律：system_prompt / LLM 配置等内部字段不进 DTO 也不展示；头像不加载图片（仅占位）；
// 空值字段直接不显示，不编造占位文本；角色名/简介等用户内容原文展示、不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
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

  @override
  void initState() {
    super.initState();
    _reload();
    _loadExtras();
  }

  Future<void> _loadExtras() async {
    try {
      final rel = await _api.getRelationship(widget.character.id);
      final facts = await _api.getWorldFacts(widget.character.id);
      final lorebook = await _api.getLorebook(widget.character.id);
      final mind = await _api.getAgentMind(widget.character.id);
      final history = await _api.getStateHistory(widget.character.id);
      if (!mounted) return;
      setState(() {
        _relationship = rel;
        _facts = facts;
        _lorebook = lorebook;
        _agentMind = mind;
        _stateHistory = history;
      });
    } catch (_) {
      // 角色主资料仍可读；关系/世界事实失败不阻塞页面。
    }
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

  Future<void> _reloadExtras() async {
    try {
      final facts = await _api.getWorldFacts(widget.character.id);
      final lorebook = await _api.getLorebook(widget.character.id);
      if (!mounted) return;
      setState(() => _lorebook = lorebook);
      if (mounted) setState(() => _facts = facts);
    } catch (_) {}
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

  Future<void> _editLorebook([ReaderLorebookEntry? entry]) async {
    final title = TextEditingController(text: entry?.title ?? '');
    final content = TextEditingController(text: entry?.content ?? '');
    final keywords = TextEditingController(text: entry?.keywords ?? '');
    var active = entry?.active ?? true;
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: Text(entry == null
              ? widget.rs.t('addLorebook')
              : widget.rs.t('editLorebook')),
          content: SizedBox(
            width: 430,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(
                controller: title,
                decoration: InputDecoration(hintText: widget.rs.t('lorebookTitle')),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: content,
                maxLines: 4,
                decoration: InputDecoration(hintText: widget.rs.t('lorebookContent')),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: keywords,
                decoration: InputDecoration(hintText: widget.rs.t('lorebookKeywords')),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(widget.rs.t('activeLabel')),
                value: active,
                onChanged: (v) => setDlg(() => active = v),
              ),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: Text(widget.rs.t('cancel'))),
            FilledButton(
              onPressed: () async {
                final titleText = title.text.trim();
                final body = content.text.trim();
                if (titleText.isEmpty || body.isEmpty) return;
                final kw = keywords.text
                    .split(RegExp(r'[,，、]'))
                    .map((s) => s.trim())
                    .where((s) => s.isNotEmpty)
                    .toList();
                try {
                  if (entry == null) {
                    await widget.editor.createLorebook(widget.character.id,
                        title: titleText, content: body, keywords: kw, active: active);
                  } else {
                    await widget.editor.updateLorebook(widget.character.id, entry.id,
                        title: titleText, content: body, keywords: kw, active: active);
                  }
                  if (ctx.mounted) Navigator.pop(ctx, true);
                } catch (e) {
                  if (ctx.mounted) {
                    ScaffoldMessenger.of(ctx).showSnackBar(
                        SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
                  }
                }
              },
              child: Text(widget.rs.t('save')),
            ),
          ],
        ),
      ),
    );
    if (saved == true) await _reloadExtras();
  }

  Future<void> _deleteLorebook(ReaderLorebookEntry entry) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('delete')),
        content: Text(entry.title),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.editor.deleteLorebook(widget.character.id, entry.id);
      await _reloadExtras();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Future<void> _editWorldFact([ReaderWorldFact? fact]) async {
    final predicate = TextEditingController(text: fact?.predicate ?? '');
    final content = TextEditingController(text: fact?.objectValue ?? '');
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(fact == null
            ? widget.rs.t('addWorldFact')
            : widget.rs.t('editWorldFact')),
        content: SizedBox(
          width: 430,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(
              controller: predicate,
              decoration:
                  InputDecoration(hintText: widget.rs.t('worldPredicate')),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: content,
              maxLines: 4,
              decoration:
                  InputDecoration(hintText: widget.rs.t('worldContent')),
            ),
          ]),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
            onPressed: () async {
              final body = content.text.trim();
              if (body.isEmpty) return;
              final p = predicate.text.trim();
              try {
                if (fact == null) {
                  await widget.editor.createWorldFact(widget.character.id,
                      content: body, predicate: p.isEmpty ? null : p);
                } else {
                  await widget.editor.updateWorldFact(
                      widget.character.id, fact.id,
                      content: body, predicate: p.isEmpty ? null : p);
                }
                if (ctx.mounted) Navigator.pop(ctx, true);
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(
                      SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
                }
              }
            },
            child: Text(widget.rs.t('save')),
          ),
        ],
      ),
    );
    if (saved == true) await _reloadExtras();
  }

  Future<void> _deleteWorldFact(ReaderWorldFact fact) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('delete')),
        content: Text(fact.objectValue),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.editor.deleteWorldFact(widget.character.id, fact.id);
      await _reloadExtras();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
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
            if (widget.onEdit != null)
              Align(
                alignment: Alignment.centerRight,
                child: OutlinedButton.icon(
                  onPressed: widget.onEdit,
                  icon: const Icon(Icons.edit, size: 18),
                  label: Text(widget.editLabel),
                ),
              ),
            // 头像占位（本单不加载图片）
            CircleAvatar(
              radius: 32,
              child: Text(c.name.isEmpty ? '?' : c.name.characters.first),
            ),
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
            if (_relationship != null) ...[
              const Divider(height: 28),
              Text(rs.t('relationMeta'),
                  style: Theme.of(context).textTheme.titleSmall),
              _row(context, rs.t('relationType'),
                  _relationship!.relationType),
              if (_relationship!.isPartner)
                _row(context, rs.t('relationType'), rs.t('partner')),
            ],
            if (_lorebook.isNotEmpty) ...[
              const Divider(height: 28),
              Row(children: [
                Expanded(
                  child: Text(rs.t('lorebook'),
                      style: Theme.of(context).textTheme.titleSmall),
                ),
                IconButton(
                  tooltip: rs.t('addLorebook'),
                  onPressed: () => _editLorebook(),
                  icon: const Icon(Icons.add, size: 18),
                ),
              ]),
              for (final e in _lorebook)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Expanded(
                        child: Text(e.title,
                            style: Theme.of(context).textTheme.titleSmall),
                      ),
                      if (!e.active)
                        Text(rs.t('inactive'),
                            style: Theme.of(context).textTheme.bodySmall),
                      IconButton(
                        tooltip: rs.t('editLorebook'),
                        onPressed: () => _editLorebook(e),
                        icon: const Icon(Icons.edit_outlined, size: 17),
                      ),
                      IconButton(
                        tooltip: rs.t('delete'),
                        onPressed: () => _deleteLorebook(e),
                        icon: const Icon(Icons.delete_outline, size: 17),
                      ),
                    ]),
                    if (e.keywords.isNotEmpty)
                      Text('${rs.t('keywords')}: ${e.keywords}',
                          style: Theme.of(context).textTheme.bodySmall),
                    const SizedBox(height: 2),
                    Text(e.content),
                  ]),
                ),
            ],
            if ((_stateHistory?['points'] as List?)?.isNotEmpty ?? false) ...[
              const Divider(height: 28),
              Text(rs.t('stateHistory'),
                  style: Theme.of(context).textTheme.titleSmall),
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
              Text(rs.t('agentMind'),
                  style: Theme.of(context).textTheme.titleSmall),
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
            if (_facts.isNotEmpty) ...[
              const Divider(height: 28),
              Row(children: [
                Expanded(
                  child: Text(rs.t('worldFacts'),
                      style: Theme.of(context).textTheme.titleSmall),
                ),
                IconButton(
                  tooltip: rs.t('addWorldFact'),
                  onPressed: () => _editWorldFact(),
                  icon: const Icon(Icons.add, size: 18),
                ),
              ]),
              for (final f in _facts)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(
                      child: Text('${f.predicate}: ${f.objectValue}',
                          style: Theme.of(context).textTheme.bodyMedium),
                    ),
                    if (f.isAuthoritative)
                      Container(
                        margin: const EdgeInsets.only(left: 6),
                        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.primaryContainer,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(rs.t('authoritative'),
                            style: Theme.of(context).textTheme.bodySmall),
                      ),
                    IconButton(
                      tooltip: rs.t('editWorldFact'),
                      onPressed: () => _editWorldFact(f),
                      icon: const Icon(Icons.edit_outlined, size: 17),
                    ),
                    IconButton(
                      tooltip: rs.t('delete'),
                      onPressed: () => _deleteWorldFact(f),
                      icon: const Icon(Icons.delete_outline, size: 17),
                    ),
                  ]),
                ),
            ],
          ],
        );
      },
    );
  }
}
