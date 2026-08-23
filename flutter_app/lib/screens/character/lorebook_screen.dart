import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";
import "package:ai_companion/theme/tokens.dart";

/// Lorebook 条目管理（P1-2）：关键词触发注入的既定设定
class LorebookScreen extends StatefulWidget {
  const LorebookScreen({super.key, required this.characterId});

  final int characterId;

  @override
  State<LorebookScreen> createState() => _LorebookScreenState();
}

class _LorebookScreenState extends State<LorebookScreen> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final items = await ApiClient().getLorebook(widget.characterId);
      if (!mounted) return;
      setState(() { _items = items; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = "$e"; _loading = false; });
    }
  }

  Future<void> _openEdit([Map<String, dynamic>? item]) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => _LorebookEditDialog(characterId: widget.characterId, item: item),
    );
    if (saved == true) _load();
  }

  Future<void> _delete(Map<String, dynamic> item) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.confirmDelete),
        content: Text("${item['title'] ?? ''}"),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ApiClient().deleteLorebook(widget.characterId, item['id'] as int);
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("$e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.lorebookTitle),
        centerTitle: false,
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: l10n.lorebookAdd,
            onPressed: () => _openEdit(),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(l10n.loadFailed, style: const TextStyle(color: IosCardColors.subtitle)),
                      const SizedBox(height: 12),
                      OutlinedButton(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.only(top: 8, bottom: 24),
                    children: [
                      if (_items.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(l10n.lorebookEmpty,
                              textAlign: TextAlign.center,
                              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                        )
                      else
                        IosCardGroup(
                          title: l10n.lorebookTitle,
                          children: [
                            for (int i = 0; i < _items.length; i++) ...[
                              if (i > 0) const IosCardDivider(),
                              ListTile(
                                title: Text(
                                  "${_items[i]['title'] ?? ''}",
                                  style: TextStyle(
                                    fontSize: 15,
                                    color: (_items[i]['active'] == true)
                                        ? scheme.onSurface
                                        : scheme.onSurface.withValues(alpha: 0.4),
                                  ),
                                ),
                                subtitle: Text(
                                  "${_items[i]['content'] ?? ''}",
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                                ),
                                trailing: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(
                                      (_items[i]['active'] == true)
                                          ? Icons.check_circle_outline
                                          : Icons.pause_circle_outline,
                                      size: 18,
                                      color: (_items[i]['active'] == true)
                                          ? Colors.green
                                          : IosCardColors.subtitle,
                                    ),
                                    const SizedBox(width: 4),
                                    const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
                                  ],
                                ),
                                onTap: () => _openEdit(_items[i]),
                                onLongPress: () => _delete(_items[i]),
                              ),
                            ],
                          ],
                        ),
                    ],
                  ),
                ),
    );
  }
}

class _LorebookEditDialog extends StatefulWidget {
  const _LorebookEditDialog({required this.characterId, this.item});

  final int characterId;
  final Map<String, dynamic>? item;

  @override
  State<_LorebookEditDialog> createState() => _LorebookEditDialogState();
}

class _LorebookEditDialogState extends State<_LorebookEditDialog> {
  late final TextEditingController _title;
  late final TextEditingController _content;
  late final TextEditingController _keywords;
  late final TextEditingController _exclude;
  late bool _active;
  bool _saving = false;

  String _join(List<dynamic>? list) {
    if (list == null) return "";
    return list.map((e) => "$e").join(",");
  }

  @override
  void initState() {
    super.initState();
    final item = widget.item;
    _title = TextEditingController(text: item?['title'] as String? ?? "");
    _content = TextEditingController(text: item?['content'] as String? ?? "");
    _keywords = TextEditingController(text: _join(item?['keywords'] as List?));
    _exclude = TextEditingController(text: _join(item?['exclude_keywords'] as List?));
    _active = item?['active'] != false;
  }

  @override
  void dispose() {
    _title.dispose();
    _content.dispose();
    _keywords.dispose();
    _exclude.dispose();
    super.dispose();
  }

  List<String> _split(String raw) =>
      raw.split(",").map((s) => s.trim()).where((s) => s.isNotEmpty).toList();

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    if (_title.text.trim().isEmpty || _content.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.lorebookEmpty)));
      return;
    }
    setState(() => _saving = true);
    try {
      final api = ApiClient();
      final item = widget.item;
      if (item == null) {
        await api.createLorebook(widget.characterId,
            title: _title.text.trim(), content: _content.text.trim(),
            keywords: _split(_keywords.text), excludeKeywords: _split(_exclude.text),
            active: _active);
      } else {
        await api.updateLorebook(widget.characterId, item['id'] as int,
            title: _title.text.trim(), content: _content.text.trim(),
            keywords: _split(_keywords.text), excludeKeywords: _split(_exclude.text),
            active: _active);
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("$e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AlertDialog(
      title: Text(widget.item == null ? l10n.lorebookAdd : l10n.lorebookEdit),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _title,
              decoration: InputDecoration(labelText: l10n.lorebookTitleField),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _content,
              maxLines: 3,
              decoration: InputDecoration(
                labelText: l10n.lorebookContentField,
                helperText: l10n.lorebookStyleHint,
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _keywords,
              decoration: InputDecoration(
                labelText: l10n.lorebookKeywords,
                helperText: l10n.lorebookKeywordsHint,
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _exclude,
              decoration: InputDecoration(
                labelText: l10n.lorebookExclude,
                helperText: l10n.lorebookExcludeHint,
              ),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.lorebookActive),
              value: _active,
              onChanged: (v) => setState(() => _active = v),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: Text(l10n.cancel)),
        TextButton(
          onPressed: _saving ? null : _save,
          child: Text(l10n.save),
        ),
      ],
    );
  }
}
