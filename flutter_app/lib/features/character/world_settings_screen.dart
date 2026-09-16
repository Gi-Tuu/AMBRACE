import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";
import "package:ai_companion/theme/tokens.dart";

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
  final Set<int> _expandedIds = {};
  final Map<int, Map<String, dynamic>> _historyById = {};
  final Set<int> _historyLoading = {};

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

  /// 新增/编辑共用的输入弹窗（复用新增对话框样式）：返回输入的文本，取消返回 null。
  Future<String?> _showFactDialog({
    required String title,
    String initialValue = "",
  }) async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController(text: initialValue);
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
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
    ctrl.dispose();
    return value;
  }

  Future<void> _add() async {
    final l10n = AppLocalizations.of(context)!;
    final value = await _showFactDialog(title: l10n.worldFactAdd);
    if (value == null || value.isEmpty) return;
    try {
      await ApiClient().createWorldFact(widget.characterId, value);
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("$e")));
    }
  }

  /// 编辑任意一条事实（含策展层写入的 system 事实）：保存后该条升为用户权威设定。
  Future<void> _edit(Map<String, dynamic> item) async {
    final l10n = AppLocalizations.of(context)!;
    final value = await _showFactDialog(
      title: l10n.edit,
      initialValue: "${item['object_value'] ?? ''}",
    );
    if (value == null || value.isEmpty) return;
    if (value == item['object_value']) return;
    try {
      await ApiClient().updateWorldFact(
        widget.characterId,
        item['id'] as int,
        content: value,
      );
      _forgetHistory(item['id'] as int);   // 改动即产生新版本，作废该行历史缓存
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
      _forgetHistory(item['id'] as int);   // 该行已删，收起并丢弃其历史缓存
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("$e")));
    }
  }

  /// 作废某条事实的历史缓存与展开态（编辑/删除后调用；下次展开重新拉取）。
  void _forgetHistory(int id) {
    _historyById.remove(id);
    _historyLoading.remove(id);
    _expandedIds.remove(id);
  }

  /// 展开/收起某条事实的「修正历史」，首次展开时拉取只读历史接口（缓存避免重复请求）。
  Future<void> _toggleHistory(Map<String, dynamic> item) async {
    final id = item['id'] as int;
    final willExpand = !_expandedIds.contains(id);
    setState(() { willExpand ? _expandedIds.add(id) : _expandedIds.remove(id); });
    if (!willExpand || _historyById.containsKey(id) || _historyLoading.contains(id)) return;
    setState(() { _historyLoading.add(id); });
    try {
      final history = await ApiClient().getWorldFactHistory(widget.characterId, id);
      if (!mounted) return;
      setState(() { _historyById[id] = history; });
    } catch (_) {
      if (!mounted) return;
      // 失败不缓存空结果、并收回展开态：用户可再次点击重试（只读接口，失败无副作用）
      final l10n = AppLocalizations.of(context)!;
      setState(() { _expandedIds.remove(id); });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.worldFactHistoryLoadFailed)),
      );
    } finally {
      if (mounted) setState(() { _historyLoading.remove(id); });
    }
  }

  /// 单条事实行：标题 + 编辑/删除/历史三按钮；展开时下方显示「修正历史」版本链。
  Widget _buildFactRow(
    Map<String, dynamic> item,
    AppLocalizations l10n,
    ColorScheme scheme,
  ) {
    final id = item['id'] as int;
    final expanded = _expandedIds.contains(id);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ListTile(
          title: Text(
            "${item['object_value'] ?? ''}",
            style: TextStyle(
              fontSize: 14,
              color: item['is_authoritative'] == true
                  ? scheme.onSurface
                  : scheme.onSurface.withValues(alpha: 0.7),
            ),
          ),
          subtitle: Text(
            "author: ${item['author'] ?? ''} · ${item['predicate'] ?? ''}",
            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
          ),
          // 编辑 / 删除（含策展层写入的 system 事实，删前二次确认）+ 历史（只读展开）
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton(
                icon: Icon(
                  Icons.history,
                  size: 20,
                  color: expanded ? scheme.primary : IosCardColors.subtitle,
                ),
                tooltip: l10n.worldFactHistory,
                onPressed: () => _toggleHistory(item),
              ),
              IconButton(
                icon: const Icon(Icons.edit_outlined,
                    size: 20, color: IosCardColors.subtitle),
                tooltip: l10n.edit,
                onPressed: () => _edit(item),
              ),
              IconButton(
                icon: const Icon(Icons.delete_outline,
                    size: 20, color: AppColors.error),
                onPressed: () => _delete(item),
              ),
            ],
          ),
        ),
        if (expanded)
          Container(
            color: scheme.surface.withValues(alpha: 0.5),
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: _historyLoading.contains(id)
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                  )
                : _buildHistoryPanel(id, l10n),
          ),
      ],
    );
  }

  /// 历史面板：版本链（含当前 + 已取代旧版）。
  Widget _buildHistoryPanel(int id, AppLocalizations l10n) {
    final history = _historyById[id];
    if (history == null) {
      return const SizedBox.shrink();
    }
    final versions = (history['versions'] as List?) ?? [];
    if (versions.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(12),
        child: Text(l10n.worldFactHistoryEmpty,
            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: Text(l10n.worldFactHistory,
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600,
                  color: IosCardColors.subtitle)),
        ),
        for (final v in versions) _historyVersion(v as Map<String, dynamic>, l10n, Theme.of(context).colorScheme),
        // 后端有版本上限（超限只回最近 N 版）：用省略号提示还有更早版本，不臆造具体条数
        if (history['truncated'] == true)
          const Padding(
            padding: EdgeInsets.only(left: 12, bottom: 4),
            child: Text('…', style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          ),
      ],
    );
  }

  /// ISO 时间串 → 日期（取前 10 位），空值/异常回落 '-'。
  String _day(Object? iso) {
    final s = iso is String ? iso : '';
    return s.length >= 10 ? s.substring(0, 10) : '-';
  }

  /// 历史版本行（含值、状态、作者、时间）。
  Widget _historyVersion(Map<String, dynamic> v, AppLocalizations l10n, ColorScheme scheme) {
    final isCurrent = v['status'] == 'active';
    final author = v['author'] ?? '-';
    final asserted = _day(v['asserted_at']);
    final superseded = _day(v['superseded_at']);
    final statusText = isCurrent ? l10n.worldFactHistoryCurrentLabel : l10n.worldFactHistorySuperseded;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: isCurrent
                      ? scheme.primaryContainer
                      : IosCardColors.subtitle.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  statusText,
                  style: TextStyle(
                    fontSize: 10,
                    color: isCurrent ? scheme.primary : IosCardColors.subtitle,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  "${v['object_value'] ?? ''}",
                  style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            "${l10n.worldFactHistoryAuthor}：$author · "
            "${l10n.worldFactHistoryAsserted}：$asserted · "
            "${l10n.worldFactHistorySupersededAt}：$superseded",
            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
          ),
        ],
      ),
    );
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
                              _buildFactRow(_items[i], l10n, scheme),
                            ],
                          ],
                        ),
                    ],
                  ),
                ),
    );
  }
}
