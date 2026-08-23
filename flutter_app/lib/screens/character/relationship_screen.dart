import "package:flutter/material.dart";
import "../../services/api_client.dart";
import "../../widgets/ai_avatar.dart";
import "../../widgets/ios_card_group.dart";

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
      setState(() { _error = "加载失败: $e"; _loading = false; });
    }
  }

  String get _partnerText {
    if (_items.isEmpty) return "未设置";
    final partners = _items.where((i) => i["is_partner"] == true).toList();
    if (partners.isEmpty) return "未设置";
    final p = partners.first;
    final g = (p["gender"] as String? ?? "").toLowerCase();
    final gt = (g == "男" || g == "male") ? "男" : ((g == "女" || g == "female") ? "女" : "未知");
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
    final isPartner = item["is_partner"] == true;
    final rt = item["relation_type"] as String? ?? "朋友";
    final summary = item["relationship_summary"] as String? ?? "";
    final base = isPartner ? "我的对象 · $rt" : rt;
    return summary.isNotEmpty ? "$base｜$summary" : base;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("关系网")),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : ListView(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  children: [
                    IosCardGroup(
                      title: '我的对象',
                      children: [
                        ListTile(
                          leading: Icon(Icons.favorite, color: Theme.of(context).colorScheme.tertiary),
                          title: Text(_partnerText, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: const Text("对象身份与性别以此处为准，AI 不会默认你的对象是异性"),
                        ),
                      ],
                    ),
                    if (_items.isNotEmpty)
                      IosCardGroup(
                        title: '全部角色关系',
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("保存失败: $e")));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text("设置「${widget.item["name"]}」的关系",
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: _type,
                decoration: InputDecoration(
                  labelText: "关系类型",
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                ),
                items: [for (final t in widget.relationTypes) DropdownMenuItem(value: t, child: Text(t))],
                onChanged: (v) => setState(() { if (v != null) _type = v; }),
              ),
              SwitchListTile(
                title: const Text("这是我的对象/伴侣"),
                subtitle: const Text("设为对象后，AI 会明确知道你的对象是谁（支持同性）"),
                value: _isPartner,
                onChanged: (v) => setState(() { _isPartner = v; }),
              ),
              TextField(
                controller: _summaryCtrl,
                maxLines: 2,
                decoration: InputDecoration(
                  labelText: "关系描述（可选）",
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                  hintText: "例如：互称老公，关系亲密",
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text("保存"),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
