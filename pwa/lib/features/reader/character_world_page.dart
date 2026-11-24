// PWA「世界设定」页（2026-11-24）：织库设定 + 世界事实的列表与增删改。
//
// 由角色详情页内联区段迁出为独立页（PWA 角色设置「世界」组与角色详情入口共用同一实现），
// 读写全部走 core 的 `ReaderApi`（只读）与 `EditorApi`（写）：
// - 织库：GET/POST/PUT/DELETE /characters/{id}/lorebook
// - 世界事实：GET/POST/PUT/DELETE /characters/{id}/world-facts
// 纪律：正文/标题用户内容原文展示、不翻译；删除必须二次确认；失败提示不吞。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

class CharacterWorldPage extends StatefulWidget {
  const CharacterWorldPage({
    super.key,
    required this.client,
    required this.editor,
    required this.characterId,
    required this.characterName,
    required this.rs,
  });

  final AmbApiClient client;
  final EditorApi editor;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;

  @override
  State<CharacterWorldPage> createState() => _CharacterWorldPageState();
}

class _CharacterWorldPageState extends State<CharacterWorldPage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  List<ReaderLorebookEntry> _lorebook = const [];
  List<ReaderWorldFact> _facts = const [];
  bool _loading = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final lorebook = await _api.getLorebook(widget.characterId);
      final facts = await _api.getWorldFacts(widget.characterId);
      if (!mounted) return;
      setState(() {
        _lorebook = lorebook;
        _facts = facts;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
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
                decoration:
                    InputDecoration(hintText: widget.rs.t('lorebookTitle')),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: content,
                maxLines: 4,
                decoration:
                    InputDecoration(hintText: widget.rs.t('lorebookContent')),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: keywords,
                decoration:
                    InputDecoration(hintText: widget.rs.t('lorebookKeywords')),
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
                    await widget.editor.createLorebook(widget.characterId,
                        title: titleText,
                        content: body,
                        keywords: kw,
                        active: active);
                  } else {
                    await widget.editor.updateLorebook(
                        widget.characterId, entry.id,
                        title: titleText,
                        content: body,
                        keywords: kw,
                        active: active);
                  }
                  if (ctx.mounted) Navigator.pop(ctx, true);
                } catch (e) {
                  if (ctx.mounted) {
                    ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(
                        content: Text('${widget.rs.t('actionFailed')}：$e')));
                  }
                }
              },
              child: Text(widget.rs.t('save')),
            ),
          ],
        ),
      ),
    );
    if (saved == true) await _load();
  }

  Future<void> _deleteLorebook(ReaderLorebookEntry entry) async {
    final ok = await _confirm(entry.title);
    if (ok != true) return;
    try {
      await widget.editor.deleteLorebook(widget.characterId, entry.id);
      await _load();
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
        title: Text(
            fact == null ? widget.rs.t('addWorldFact') : widget.rs.t('editWorldFact')),
        content: SizedBox(
          width: 430,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(
              controller: predicate,
              decoration: InputDecoration(hintText: widget.rs.t('worldPredicate')),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: content,
              maxLines: 4,
              decoration: InputDecoration(hintText: widget.rs.t('worldContent')),
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
                  await widget.editor.createWorldFact(widget.characterId,
                      content: body, predicate: p.isEmpty ? null : p);
                } else {
                  await widget.editor.updateWorldFact(widget.characterId, fact.id,
                      content: body, predicate: p.isEmpty ? null : p);
                }
                if (ctx.mounted) Navigator.pop(ctx, true);
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(
                      content: Text('${widget.rs.t('actionFailed')}：$e')));
                }
              }
            },
            child: Text(widget.rs.t('save')),
          ),
        ],
      ),
    );
    if (saved == true) await _load();
  }

  Future<void> _deleteWorldFact(ReaderWorldFact fact) async {
    final ok = await _confirm(fact.objectValue);
    if (ok != true) return;
    try {
      await widget.editor.deleteWorldFact(widget.characterId, fact.id);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Future<bool?> _confirm(String body) => showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(widget.rs.t('delete')),
          content: Text(body, maxLines: 4, overflow: TextOverflow.ellipsis),
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

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text('${rs.t('worldSettings')} · ${widget.characterName}')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(_error,
                          textAlign: TextAlign.center,
                          style: TextStyle(color: theme.colorScheme.error)),
                    ),
                    OutlinedButton(onPressed: _load, child: Text(rs.t('retry'))),
                  ]),
                )
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 28),
                    children: [
                      _sectionHeader(
                        rs.t('lorebook'),
                        onAdd: () => _editLorebook(),
                      ),
                      if (_lorebook.isEmpty)
                        _emptyTile(rs.t('lorebookEmpty'))
                      else
                        Card(
                          margin: EdgeInsets.zero,
                          child: Column(children: [
                            for (final e in _lorebook) ...[
                              ListTile(
                                dense: true,
                                leading: Icon(
                                    e.active
                                        ? Icons.check_circle_outline
                                        : Icons.remove_circle_outline,
                                    size: 18),
                                title: Text(e.title,
                                    maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(
                                  [
                                    if (e.keywords.trim().isNotEmpty) e.keywords,
                                    e.content,
                                  ].join(' · '),
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                ),
                                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                                  IconButton(
                                    icon: const Icon(Icons.edit_outlined, size: 18),
                                    tooltip: rs.t('editLorebook'),
                                    onPressed: () => _editLorebook(e),
                                  ),
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, size: 18),
                                    onPressed: () => _deleteLorebook(e),
                                  ),
                                ]),
                              ),
                              if (e != _lorebook.last) const Divider(height: 1),
                            ],
                          ]),
                        ),
                      const SizedBox(height: 14),
                      _sectionHeader(
                        rs.t('worldFacts'),
                        onAdd: () => _editWorldFact(),
                      ),
                      if (_facts.isEmpty)
                        _emptyTile(rs.t('worldFactsEmpty'))
                      else
                        Card(
                          margin: EdgeInsets.zero,
                          child: Column(children: [
                            for (final f in _facts) ...[
                              ListTile(
                                dense: true,
                                leading: Icon(
                                    f.isAuthoritative
                                        ? Icons.verified_outlined
                                        : Icons.help_outline,
                                    size: 18),
                                title: Text(f.predicate.isEmpty ? rs.t('worldFacts') : f.predicate,
                                    maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(f.objectValue,
                                    maxLines: 2, overflow: TextOverflow.ellipsis),
                                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                                  IconButton(
                                    icon: const Icon(Icons.edit_outlined, size: 18),
                                    tooltip: rs.t('editWorldFact'),
                                    onPressed: () => _editWorldFact(f),
                                  ),
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, size: 18),
                                    onPressed: () => _deleteWorldFact(f),
                                  ),
                                ]),
                              ),
                              if (f != _facts.last) const Divider(height: 1),
                            ],
                          ]),
                        ),
                    ],
                  ),
                ),
    );
  }

  Widget _sectionHeader(String title, {required VoidCallback onAdd}) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 10, 4, 6),
        child: Row(children: [
          Expanded(
            child: Text(title,
                style: Theme.of(context).textTheme.titleSmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ),
          IconButton(
            icon: const Icon(Icons.add, size: 20),
            tooltip: widget.rs.t('add'),
            onPressed: onAdd,
          ),
        ]),
      );

  Widget _emptyTile(String text) => Padding(
        padding: const EdgeInsets.fromLTRB(8, 4, 8, 4),
        child: Text(text,
            style: TextStyle(
                fontSize: 12, color: Theme.of(context).colorScheme.outline)),
      );
}

