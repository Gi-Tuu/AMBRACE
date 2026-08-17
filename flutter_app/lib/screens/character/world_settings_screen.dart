import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";

/// 世界设定管理（P1-3）：用户定义的不可动摇事实（AI 推断不能覆盖）
class WorldSettingsScreen extends StatefulWidget {
  const WorldSettingsScreen({super.key, required this.characterId});

  final int characterId;

  @override
  State<WorldSettingsScreen> createState() => _WorldSettingsScreenState();
}

class _WorldSettingsScreenState extends State<WorldSettingsScreen> {
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
      final items = await ApiClient().getWorldFacts(widget.characterId);
      if (!mounted) return;
      setState(() { _items = items; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = "$e"; _loading = false; });
    }
  }

  Future<void> _add() async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.worldFactAdd),
        content: TextField(
          controller: ctrl,
          maxLines: 2,
          autofocus: true,
          decoration: InputDecoration(
            hintText: l10n.worldFactContentHint,
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: Text(l10n.save),
          ),
        ],
      ),
    );
    if (value == null || value.isEmpty) return;
    try {
      await ApiClient().createWorldFact(widget.characterId, value);
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("$e")));
    }
  }

  Future<void> _delete(Map<String, dynamic> item) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.confirmDelete),
        content: Text("${item['object_value'] ?? ''}"),
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
      await ApiClient().deleteWorldFact(widget.characterId, item['id'] as int);
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
        title: Text(l10n.worldFactsTitle),
        centerTitle: false,
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: l10n.worldFactAdd,
            onPressed: _add,
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
                          child: Text(l10n.worldFactsEmpty,
                              textAlign: TextAlign.center,
                              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                        )
                      else
                        IosCardGroup(
                          title: l10n.worldFactsTitle,
                          children: [
                            for (int i = 0; i < _items.length; i++) ...[
                              if (i > 0) const IosCardDivider(),
                              ListTile(
                                title: Text(
                                  "${_items[i]['object_value'] ?? ''}",
                                  style: TextStyle(
                                    fontSize: 14,
                                    color: _items[i]['is_authoritative'] == true
                                        ? scheme.onSurface
                                        : scheme.onSurface.withValues(alpha: 0.7),
                                  ),
                                ),
                                subtitle: Text(
                                  "author: ${_items[i]['author'] ?? ''} · ${_items[i]['predicate'] ?? ''}",
                                  style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                                ),
                                trailing: _items[i]['author'] == "user"
                                    ? IconButton(
                                        icon: const Icon(Icons.delete_outline,
                                            size: 20, color: Color(0xFFFF3B30)),
                                        onPressed: () => _delete(_items[i]),
                                      )
                                    : const Icon(Icons.lock_outline, size: 16, color: IosCardColors.subtitle),
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
