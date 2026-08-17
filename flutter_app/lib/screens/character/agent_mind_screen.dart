import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";

/// AI 内心世界（Phase J/P1，2026-08-16）：最近复盘 + 任务记录 + 工具使用轨迹
class AgentMindScreen extends StatefulWidget {
  const AgentMindScreen({super.key, required this.characterId, required this.characterName});

  final int characterId;
  final String characterName;

  @override
  State<AgentMindScreen> createState() => _AgentMindScreenState();
}

class _AgentMindScreenState extends State<AgentMindScreen> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _data;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final data = await ApiClient().getAgentMind(widget.characterId);
      if (!mounted) return;
      setState(() { _data = data; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = "$e"; _loading = false; });
    }
  }

  String _fmtTime(String? iso) {
    if (iso == null || iso.isEmpty) return "";
    try {
      final t = DateTime.parse(iso).toLocal();
      return "${t.year}-${t.month.toString().padLeft(2, "0")}-${t.day.toString().padLeft(2, "0")} "
          "${t.hour.toString().padLeft(2, "0")}:${t.minute.toString().padLeft(2, "0")}";
    } catch (_) {
      return "";
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.agentMind),
        centerTitle: false,
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
                      _buildMemorySearch(l10n, scheme),
                      _buildRunningNotes(l10n, scheme),
                      _buildReflection(l10n, scheme),
                      _buildTasks(l10n, scheme),
                      _buildToolLogs(l10n, scheme),
                    ],
                  ),
                ),
    );
  }

  Widget _buildReflection(AppLocalizations l10n, ColorScheme scheme) {
    final refl = (_data?["reflection"] as Map?)?.cast<String, dynamic>();
    final content = (refl?["content"] as String?)?.trim() ?? "";
    return IosCardGroup(title: l10n.agentMindReflection, children: [
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: content.isEmpty
            ? Text(l10n.agentMindEmpty, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle))
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(content, style: TextStyle(fontSize: 14, height: 1.6, color: scheme.onSurface)),
                  if (_fmtTime(refl?["created_at"] as String?).isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(_fmtTime(refl?["created_at"] as String?),
                          style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                    ),
                ],
              ),
      ),
    ]);
  }

  Widget _buildMemorySearch(AppLocalizations l10n, ColorScheme scheme) {
    final ms = (_data?["memory_search"] as Map?)?.cast<String, dynamic>();
    final total = (ms?["total"] as num?)?.toInt() ?? 0;
    if (total == 0) return const SizedBox.shrink();
    final hit = (ms?["hit"] as num?)?.toInt() ?? 0;
    final miss = (ms?["miss"] as num?)?.toInt() ?? 0;
    final avg = (ms?["avg_latency_ms"] as num?)?.toInt() ?? 0;
    final recent = ((ms?["recent"] as List?) ?? []).cast<Map>();
    return IosCardGroup(title: l10n.agentMindMemorySearch, children: [
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Text(
          l10n.agentMindHitSummary(hit, miss, avg),
          style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
        ),
      ),
      if (recent.isEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
          child: Text(l10n.agentMindSearchEmpty,
              style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        )
      else
        for (int i = 0; i < recent.length; i++) ...[
          if (i > 0) const IosCardDivider(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  ((recent[i]["hit_count"] as num?)?.toInt() ?? 0) > 0
                      ? Icons.check_circle_outline
                      : Icons.remove_circle_outline,
                  size: 16,
                  color: ((recent[i]["hit_count"] as num?)?.toInt() ?? 0) > 0
                      ? Colors.green
                      : IosCardColors.subtitle,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("${recent[i]["query"] ?? ""}",
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 13, color: scheme.onSurface)),
                      Text(
                        "${recent[i]["hit_count"] ?? 0} 条 · ${_fmtTime(recent[i]["created_at"] as String?)} · ${recent[i]["latency_ms"] ?? 0}ms",
                        style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
    ]);
  }

  Widget _buildRunningNotes(AppLocalizations l10n, ColorScheme scheme) {
    final notes = (_data?["running_notes"] as Map?)?.cast<String, dynamic>();
    final identity = (notes?["identity"] as Map?)?.cast<String, dynamic>();
    final pinned = ((notes?["pinned"] as List?) ?? []).cast<Map>();
    final hasIdentity = (identity?["content"] as String?)?.trim().isNotEmpty ?? false;
    final empty = !hasIdentity && pinned.isEmpty;
    return IosCardGroup(title: l10n.agentMindRunningNotes, children: [
      if (empty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Text(l10n.agentMindNoteEmpty, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        )
      else ...[
        if (hasIdentity) ...[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.agentMindIdentity,
                    style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: IosCardColors.subtitle)),
                const SizedBox(height: 4),
                Text("${identity?["content"] ?? ""}",
                    style: TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface)),
                if (_fmtTime(identity?["updated_at"] as String?).isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(_fmtTime(identity?["updated_at"] as String?),
                        style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle)),
                  ),
              ],
            ),
          ),
        ],
        for (int i = 0; i < pinned.length; i++) ...[
          if (i > 0 || hasIdentity) const IosCardDivider(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text("${pinned[i]["label"] ?? pinned[i]["memory_type"] ?? ""}",
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: IosCardColors.subtitle)),
                    const Spacer(),
                    Text(_fmtTime(pinned[i]["updated_at"] as String?),
                        style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle)),
                  ],
                ),
                const SizedBox(height: 4),
                Text("${pinned[i]["content"] ?? ""}",
                    style: TextStyle(fontSize: 13, height: 1.5, color: scheme.onSurface)),
              ],
            ),
          ),
        ],
      ],
    ]);
  }

  Widget _buildTasks(AppLocalizations l10n, ColorScheme scheme) {
    final tasks = ((_data?["tasks"] as List?) ?? []).cast<Map>();
    return IosCardGroup(title: l10n.agentMindTasks, children: [
      if (tasks.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Text(l10n.agentMindEmpty, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        )
      else
        for (int i = 0; i < tasks.length; i++) ...[
          if (i > 0) const IosCardDivider(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_statusIcon(tasks[i]["status"] as String?),
                    size: 18, color: _statusColor(tasks[i]["status"] as String?)),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("${tasks[i]["goal"] ?? ""}",
                          style: TextStyle(fontSize: 14, color: scheme.onSurface)),
                      const SizedBox(height: 2),
                      Text(
                        "${tasks[i]["trigger"] ?? ""} · ${tasks[i]["status"] ?? ""} · ${_fmtTime(tasks[i]["created_at"] as String?)}",
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
    ]);
  }

  Widget _buildToolLogs(AppLocalizations l10n, ColorScheme scheme) {
    final logs = ((_data?["tool_logs"] as List?) ?? []).cast<Map>();
    return IosCardGroup(title: l10n.agentMindToolLogs, children: [
      if (logs.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Text(l10n.agentMindEmpty, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        )
      else
        for (int i = 0; i < logs.length; i++) ...[
          if (i > 0) const IosCardDivider(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_statusIcon(logs[i]["status"] as String?),
                    size: 16, color: _statusColor(logs[i]["status"] as String?)),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("${logs[i]["trigger"] ?? ""} / ${logs[i]["route"] ?? ""}",
                          style: TextStyle(fontSize: 13, color: scheme.onSurface)),
                      if ((logs[i]["steps"] as String?)?.isNotEmpty ?? false)
                        Padding(
                          padding: const EdgeInsets.only(top: 3),
                          child: Text(
                            "${logs[i]["steps"]}",
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                          ),
                        ),
                      Text(
                        "${_fmtTime(logs[i]["created_at"] as String?)} · ${logs[i]["latency_ms"] ?? 0}ms",
                        style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
    ]);
  }

  IconData _statusIcon(String? status) {
    switch (status) {
      case "done":
      case "ok":
        return Icons.check_circle_outline;
      case "blocked":
        return Icons.block;
      case "failed":
      case "error":
        return Icons.error_outline;
      default:
        return Icons.schedule;
    }
  }

  Color _statusColor(String? status) {
    switch (status) {
      case "done":
      case "ok":
        return Colors.green;
      case "blocked":
        return Colors.orange;
      case "failed":
      case "error":
        return Colors.red;
      default:
        return Colors.blueGrey;
    }
  }
}
