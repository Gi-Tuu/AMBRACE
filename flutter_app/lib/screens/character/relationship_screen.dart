import "package:flutter/material.dart";
import "../../services/api_client.dart";
import "../../widgets/ai_avatar.dart";
import "../../widgets/ios_card_group.dart";
import "package:ai_companion/l10n/app_localizations.dart";

String _relationTypeLabel(String value, AppLocalizations l10n) {
  switch (value) {
    case "对象/伴侣":
      return l10n.relationTypePartner;
    case "老公":
      return l10n.relationTypeHusband;
    case "闺蜜":
      return l10n.relationTypeBestie;
    case "兄弟":
      return l10n.relationTypeBro;
    case "死党":
      return l10n.relationTypeBuddy;
    case "家人":
      return l10n.relationTypeFamily;
    case "朋友":
      return l10n.relationTypeFriend;
    case "其他":
      return l10n.genderOther;
    default:
      return l10n.relationTypeOther;
  }
}

/// 关系网：管理用户与每个 AI 角色的关系（关系类型/是否对象/关系描述）
class RelationshipScreen extends StatefulWidget {
  const RelationshipScreen({super.key});
  @override
  State<RelationshipScreen> createState() => _RelationshipScreenState();
}

class _RelationshipScreenState extends State<RelationshipScreen> {
  static const _relationTypes = ["对象/伴侣", "老公", "闺蜜", "兄弟", "死党", "家人", "朋友", "其他"];
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
      final data = await ApiClient().getRelationships();
      if (!mounted) return;
      setState(() {
        _items = ((data["relationships"] as List?) ?? [])
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList();
        _loading = false;
      });
    } catch (e) {
      final l10n = AppLocalizations.of(context)!;
      setState(() { _error = l10n.relationLoadFail(e.toString()); _loading = false; });
    }
  }

  String get _partnerText {
    final l10n = AppLocalizations.of(context)!;
    if (_items.isEmpty) return l10n.ppLocNotSet;
    final partners = _items.where((i) => i["is_partner"] == true).toList();
    if (partners.isEmpty) return l10n.ppLocNotSet;
    final p = partners.first;
    final g = (p["gender"] as String? ?? "").toLowerCase();
    final gt = (g == "男" || g == "male") ? l10n.genderMale : ((g == "女" || g == "female") ? l10n.genderFemale : l10n.unknown);
    return "${p["name"]}（$gt）";
  }

  Future<void> _editItem(Map<String, dynamic> item) async {
    final types = List<String>.from(_relationTypes);
    final current = item["relation_type"] as String?;
    if (current != null && !types.contains(current)) types.add(current);
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _EditRelationSheet(item: item, relationTypes: types),
    );
    if (result == true) _load();
  }

  String _relationLabel(Map<String, dynamic> item) {
    final l10n = AppLocalizations.of(context)!;
    final isPartner = item["is_partner"] == true;
    final rt = item["relation_type"] as String? ?? "朋友";
    final rtLabel = _relationTypeLabel(rt, l10n);
    final summary = item["relationship_summary"] as String? ?? "";
    final base = isPartner ? l10n.relationPartnerLabel(rtLabel) : rtLabel;
    return summary.isNotEmpty ? "$base｜$summary" : base;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.relationNetwork)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : ListView(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  children: [
                    IosCardGroup(
                      title: l10n.relationMyPartner,
                      children: [
                        ListTile(
                          leading: Icon(Icons.favorite, color: Theme.of(context).colorScheme.tertiary),
                          title: Text(_partnerText, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text(l10n.relationPartnerNote),
                        ),
                      ],
                    ),
                    if (_items.isNotEmpty)
                      IosCardGroup(
                        title: l10n.relationAllRoles,
                        children: [
                          for (var i = 0; i < _items.length; i++) ...[
                            if (i > 0) const IosCardDivider(),
                            ListTile(
                              leading: AIAvatar(name: _items[i]["name"] as String? ?? "AI", size: 40, imageUrl: _items[i]["avatar_url"] as String?),
                              title: Text(_items[i]["name"] as String? ?? ""),
                              subtitle: Text(_relationLabel(_items[i])),
                              trailing: const Icon(Icons.edit_outlined, size: 20, color: IosCardColors.chevron),
                              onTap: () => _editItem(_items[i]),
                            ),
                          ],
                        ],
                      ),
                  ],
                ),
    );
  }
}

class _EditRelationSheet extends StatefulWidget {
  final Map<String, dynamic> item;
  final List<String> relationTypes;
  const _EditRelationSheet({required this.item, required this.relationTypes});
  @override
  State<_EditRelationSheet> createState() => _EditRelationSheetState();
}

class _EditRelationSheetState extends State<_EditRelationSheet> {
  late String _type;
  late bool _isPartner;
  late final TextEditingController _summaryCtrl;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _type = widget.item["relation_type"] as String? ?? "朋友";
    _isPartner = widget.item["is_partner"] == true;
    _summaryCtrl = TextEditingController(text: widget.item["relationship_summary"] as String? ?? "");
  }

  @override
  void dispose() {
    _summaryCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() { _saving = true; });
    try {
      final charId = widget.item["character_id"] as int;
      await ApiClient().updateRelationship(charId, {
        "relation_type": _type,
        "is_partner": _isPartner,
        "relationship_summary": _summaryCtrl.text.trim(),
      });
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() { _saving = false; });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.relationSaveFail(e.toString()))));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l10n.relationSetTitle(widget.item["name"].toString()),
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _type,
                decoration: InputDecoration(
                  labelText: l10n.relationTypeLabel,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                ),
                items: [for (final t in widget.relationTypes) DropdownMenuItem(value: t, child: Text(_relationTypeLabel(t, l10n)))],
                onChanged: (v) => setState(() { if (v != null) _type = v; }),
              ),
              SwitchListTile(
                title: Text(l10n.relationIsPartner),
                subtitle: Text(l10n.relationIsPartnerHint),
                value: _isPartner,
                onChanged: (v) => setState(() { _isPartner = v; }),
              ),
              TextField(
                controller: _summaryCtrl,
                maxLines: 2,
                decoration: InputDecoration(
                  labelText: l10n.relationDescOptional,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                  hintText: l10n.relationDescHint,
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(l10n.save),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
