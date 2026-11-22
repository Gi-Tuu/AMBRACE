import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// 宠物只读面板：列表、等级、四项状态、是否需要关注。
class PetsPane extends StatefulWidget {
  const PetsPane({
    super.key,
    required this.client,
    required this.editor,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final EditorApi editor;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<PetsPane> createState() => _PetsPaneState();
}

class _PetsPaneState extends State<PetsPane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderPet>> _loader =
      ReaderLoader<List<ReaderPet>>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load((ct) => _api.getPets(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
  }

  Future<void> _action(ReaderPet pet, String action) async {
    try {
      await widget.editor.interactPet(pet.id, action);
      _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Future<void> _rename(ReaderPet pet) async {
    final ctrl = TextEditingController(text: pet.name);
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('petRename')),
        content: TextField(
            controller: ctrl,
            decoration: InputDecoration(hintText: widget.rs.t('petName'))),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
              onPressed: () {
                final v = ctrl.text.trim();
                if (v.isNotEmpty) Navigator.pop(ctx, v);
              },
              child: Text(widget.rs.t('save'))),
        ],
      ),
    );
    if (name == null) return;
    try {
      await widget.editor.renamePet(pet.id, name);
      _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Future<void> _delete(ReaderPet pet) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('petDelete')),
        content: Text(pet.name),
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
      await widget.editor.deletePet(pet.id);
      _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Future<void> _adopt() async {
    final name = TextEditingController();
    var species = 'cat';
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: Text(widget.rs.t('adoptPet')),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            DropdownButtonFormField<String>(
              initialValue: species,
              decoration: InputDecoration(labelText: widget.rs.t('petSpecies')),
              items: const [
                DropdownMenuItem(value: 'cat', child: Text('cat')),
                DropdownMenuItem(value: 'dog', child: Text('dog')),
                DropdownMenuItem(value: 'rabbit', child: Text('rabbit')),
              ],
              onChanged: (v) => setDlg(() => species = v ?? 'cat'),
            ),
            TextField(
                controller: name,
                maxLength: 5,
                decoration:
                    InputDecoration(hintText: widget.rs.t('petName'))),
          ]),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(widget.rs.t('cancel'))),
            FilledButton(
              onPressed: () {
                if (name.text.trim().isNotEmpty) {
                  Navigator.pop(ctx, '$species|${name.text.trim()}');
                }
              },
              child: Text(widget.rs.t('create')),
            ),
          ],
        ),
      ),
    );
    if (result == null) return;
    final parts = result.split('|');
    try {
      await widget.editor.adoptPet(parts[0], parts[1]);
      _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Widget _bar(BuildContext context, String label, int value) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        SizedBox(width: 56, child: Text(label, style: theme.textTheme.bodySmall)),
        Expanded(
          child: LinearProgressIndicator(value: (value / 100).clamp(0.0, 1.0)),
        ),
        const SizedBox(width: 8),
        Text('$value', style: theme.textTheme.bodySmall),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return ReaderAsyncView<List<ReaderPet>>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (pets) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(children: [
            Expanded(
              child: Text(rs.t('pets'),
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(onPressed: _adopt, icon: const Icon(Icons.add)),
            IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          ]),
          for (final p in pets)
            Card(
              margin: const EdgeInsets.only(bottom: 10),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    CircleAvatar(
                      radius: 20,
                      child: Text(p.name.isEmpty ? '?' : p.name.characters.first),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                        Text('${p.speciesLabel} · Lv.${p.level}',
                            style: Theme.of(context).textTheme.bodySmall),
                      ]),
                    ),
                    if (p.needAttention)
                      Chip(
                        label: Text(rs.t('petNeedAttention')),
                        visualDensity: VisualDensity.compact,
                      ),
                  ]),
                  const SizedBox(height: 8),
                  _bar(context, rs.t('petHunger'), p.hunger),
                  _bar(context, rs.t('petMood'), p.mood),
                  _bar(context, rs.t('petEnergy'), p.energy),
                  _bar(context, rs.t('petCleanliness'), p.cleanliness),
                  if (p.statusText.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text(p.statusText,
                          style: Theme.of(context).textTheme.bodySmall),
                    ),
                  Wrap(spacing: 2, children: [
                    TextButton(
                        onPressed: () => _action(p, 'feed'),
                        child: Text(rs.t('petFeed'))),
                    TextButton(
                        onPressed: () => _action(p, 'play'),
                        child: Text(rs.t('petPlay'))),
                    TextButton(
                        onPressed: () => _action(p, 'clean'),
                        child: Text(rs.t('petClean'))),
                    TextButton(
                        onPressed: () => _rename(p),
                        child: Text(rs.t('petRename'))),
                    TextButton(
                        onPressed: () => _delete(p),
                        child: Text(rs.t('petDelete'))),
                  ]),
                ]),
              ),
            ),
        ],
      ),
    );
  }
}
