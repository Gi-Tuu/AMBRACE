// PWA「AI 内心世界」整页（2026-11-24）：对齐 App `AgentMindScreen` 的五个分区。
//
// 数据单一来源 `GET /api/v1/characters/{id}/agent-mind`（只读，零写请求）：
// - 记忆检索：memory_search{total,hit,miss,avg_latency_ms,recent[]}（total=0 时整段隐藏）
// - 运行笔记：running_notes{identity,pinned[]}
// - 最近复盘：reflection{content,created_at}
// - 任务记录：tasks[]（无正式任务时后端已用 AgentTaskLog 摘要兜底，前端不另造）
// - 工具日志：tool_logs[] + scheduler_trace.skipped_recent + 账号级 mcp_calls[]
//
// 纪律：状态口径与 App 同款（success/partial/failed/blocked/skipped，blocked≠失败、
// skipped≠失败）；空值不编造占位文本；用户内容（笔记/复盘正文）原文展示、不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

class AgentMindPage extends StatefulWidget {
  const AgentMindPage({
    super.key,
    required this.client,
    required this.characterId,
    required this.characterName,
    required this.rs,
  });

  final AmbApiClient client;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;

  @override
  State<AgentMindPage> createState() => _AgentMindPageState();
}

class _AgentMindPageState extends State<AgentMindPage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  Map<String, dynamic>? _data;
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
      final data = await _api.getAgentMind(widget.characterId);
      if (!mounted) return;
      setState(() {
        _data = data;
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

  Map<String, dynamic> _map(Object? v) =>
      v is Map ? v.cast<String, dynamic>() : const {};

  List<Map<String, dynamic>> _maps(Object? v) => v is List
      ? [for (final e in v) if (e is Map) e.cast<String, dynamic>()]
      : const [];

  String _fmtTime(Object? iso) {
    if (iso is! String || iso.isEmpty) return '';
    try {
      final t = DateTime.parse(iso).toLocal();
      return '${t.year}-${t.month.toString().padLeft(2, '0')}-'
          '${t.day.toString().padLeft(2, '0')} '
          '${t.hour.toString().padLeft(2, '0')}:'
          '${t.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }

  /// 统一执行状态口径（与 App `_classifyStatus` 逐条对齐）。
  String _classifyStatus(Object? status) {
    switch (status) {
      case 'ok':
      case 'done':
      case 'success':
      case 'succeeded':
        return 'success';
      case 'error':
      case 'failed':
      case 'failure':
        return 'failed';
      case 'partial':
      case 'partial_success':
        return 'partial';
      case 'skipped':
      case 'not_attempted':
        return 'skipped';
      case 'blocked':
      case 'intercepted':
        return 'blocked';
      default:
        return 'unknown';
    }
  }

  IconData _statusIcon(Object? status) {
    switch (_classifyStatus(status)) {
      case 'success':
        return Icons.check_circle_outline;
      case 'failed':
        return Icons.error_outline;
      case 'blocked':
        return Icons.block;
      case 'skipped':
        return Icons.skip_next_outlined;
      default:
        return Icons.schedule;
    }
  }

  Color _statusColor(Object? status) {
    switch (_classifyStatus(status)) {
      case 'success':
        return Colors.green;
      case 'failed':
        return Colors.red;
      case 'blocked':
        return Colors.orange;
      default:
        return Colors.blueGrey;
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('${rs.t('agentMind')} · ${widget.characterName}'),
              Text(rs.t('agentMindSubtitle'),
                  style: Theme.of(context).textTheme.labelSmall),
            ],
          ),
          bottom: TabBar(
            isScrollable: true,
            tabs: [
              Tab(text: rs.t('agentMindMemorySearch')),
              Tab(text: rs.t('agentMindRunningNotes')),
              Tab(text: rs.t('reflection')),
              Tab(text: rs.t('tasks')),
              Tab(text: rs.t('agentMindToolLogs')),
            ],
          ),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error.isNotEmpty
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(_error,
                            textAlign: TextAlign.center,
                            style: TextStyle(
                                color: Theme.of(context).colorScheme.error)),
                      ),
                      OutlinedButton(
                          onPressed: _load, child: Text(rs.t('retry'))),
                    ]),
                  )
                : RefreshIndicator(
                    onRefresh: _load,
                    child: TabBarView(children: [
                      _tabList(_buildMemorySearch()),
                      _tabList(_buildRunningNotes()),
                      _tabList(_buildReflection()),
                      _tabList(_buildTasks()),
                      _tabList(_buildToolLogs()),
                    ]),
                  ),
      ),
    );
  }

  Widget _tabList(List<Widget> children) => ListView(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 28),
        children: children,
      );

  Widget _sectionTitle(String text) => Padding(
        padding: const EdgeInsets.only(left: 4, top: 4, bottom: 6),
        child: Text(text,
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant)),
      );

  Widget _empty(String text) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 16),
        child: Text(text,
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: Theme.of(context).colorScheme.outline)),
      );

  Widget _statCard(String label, String value) {
    final theme = Theme.of(context);
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
          child: Column(children: [
            Text(value,
                style: theme.textTheme.titleLarge
                    ?.copyWith(color: theme.colorScheme.primary)),
            const SizedBox(height: 2),
            Text(label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.labelSmall),
          ]),
        ),
      ),
    );
  }

  // ── 1. 记忆检索 ────────────────────────────────────────────────
  List<Widget> _buildMemorySearch() {
    final rs = widget.rs;
    final ms = _map(_data?['memory_search']);
    final total = (ms['total'] as num?)?.toInt() ?? 0;
    if (total == 0) {
      return [
        _sectionTitle(rs.t('agentMindMemorySearch')),
        _empty(rs.t('agentMindSearchEmpty')),
      ];
    }
    final hit = (ms['hit'] as num?)?.toInt() ?? 0;
    final miss = (ms['miss'] as num?)?.toInt() ?? 0;
    final avg = (ms['avg_latency_ms'] as num?)?.toInt() ?? 0;
    final recent = _maps(ms['recent']).take(25).toList();
    return [
      Row(children: [
        _statCard(rs.t('agentMindStatHit'), '$hit'),
        const SizedBox(width: 8),
        _statCard(rs.t('agentMindStatMiss'), '$miss'),
        const SizedBox(width: 8),
        _statCard(rs.t('agentMindStatAvgLatency'), '${avg}ms'),
      ]),
      const SizedBox(height: 6),
      _sectionTitle(rs.t('agentMindMemorySearch')),
      if (recent.isEmpty)
        _empty(rs.t('agentMindSearchEmpty'))
      else
        for (final item in recent) _memorySearchCard(item),
    ];
  }

  Widget _memorySearchCard(Map<String, dynamic> item) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final hit = (item['hit_count'] as num?)?.toInt() ?? 0;
    final returned = (item['returned'] as num?)?.toInt() ?? hit;
    final time = _fmtTime(item['created_at']);
    final ms = (item['latency_ms'] ?? 0).toString();
    final countText = returned != hit
        ? '${rs.t('agentMindCandidates')} $hit · ${rs.t('agentMindReturned')} $returned'
        : '${rs.t('agentMindCandidates')} $hit';
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        dense: true,
        leading: Icon(
          hit > 0 ? Icons.check_circle_outline : Icons.remove_circle_outline,
          size: 18,
          color: hit > 0 ? Colors.green : theme.colorScheme.outline,
        ),
        title: Text('${item['query'] ?? ''}',
            maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text([countText, if (time.isNotEmpty) time, '${ms}ms'].join(' · ')),
      ),
    );
  }

  // ── 2. 运行笔记 ────────────────────────────────────────────────
  List<Widget> _buildRunningNotes() {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final notes = _map(_data?['running_notes']);
    final identity = _map(notes['identity']);
    final pinned = _maps(notes['pinned']).take(25).toList();
    final identityText = (identity['content'] as String?)?.trim() ?? '';
    final empty = identityText.isEmpty && pinned.isEmpty;
    return [
      _sectionTitle(rs.t('agentMindRunningNotes')),
      if (empty)
        _empty(rs.t('agentMindNoteEmpty'))
      else ...[
        if (identityText.isNotEmpty)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(rs.t('agentMindIdentity'),
                      style: theme.textTheme.labelSmall),
                  const SizedBox(height: 4),
                  Text(identityText, style: theme.textTheme.bodyMedium),
                  if (_fmtTime(identity['updated_at']).isNotEmpty)
                    Text(_fmtTime(identity['updated_at']),
                        style: theme.textTheme.labelSmall),
                ],
              ),
            ),
          ),
        for (final p in pinned)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [
                    Expanded(
                      child: Text('${p['label'] ?? p['memory_type'] ?? ''}',
                          style: theme.textTheme.labelSmall),
                    ),
                    Text(_fmtTime(p['updated_at']),
                        style: theme.textTheme.labelSmall),
                  ]),
                  const SizedBox(height: 4),
                  Text('${p['content'] ?? ''}',
                      style: theme.textTheme.bodyMedium),
                ],
              ),
            ),
          ),
      ],
    ];
  }

  // ── 3. 最近复盘 ────────────────────────────────────────────────
  List<Widget> _buildReflection() {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final refl = _map(_data?['reflection']);
    final content = (refl['content'] as String?)?.trim() ?? '';
    return [
      _sectionTitle(rs.t('reflection')),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: content.isEmpty
              ? Text(rs.t('agentMindEmpty'), style: theme.textTheme.bodySmall)
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(content, style: theme.textTheme.bodyMedium),
                    if (_fmtTime(refl['created_at']).isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(_fmtTime(refl['created_at']),
                            style: theme.textTheme.labelSmall),
                      ),
                  ],
                ),
        ),
      ),
    ];
  }

  // ── 4. 任务记录 ────────────────────────────────────────────────
  List<Widget> _buildTasks() {
    final rs = widget.rs;
    final tasks = _maps(_data?['tasks']).take(25).toList();
    return [
      _sectionTitle(rs.t('tasks')),
      if (tasks.isEmpty) _empty(rs.t('agentMindEmpty')),
      for (final t in tasks)
        Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            dense: true,
            leading: Icon(_statusIcon(t['status']),
                size: 18, color: _statusColor(t['status'])),
            title: Text('${t['goal'] ?? ''}'),
            subtitle: Text([
              '${t['trigger'] ?? ''}',
              '${t['status'] ?? ''}',
              if (_fmtTime(t['created_at']).isNotEmpty) _fmtTime(t['created_at']),
            ].join(' · ')),
          ),
        ),
    ];
  }

  // ── 5. 工具日志（含 MCP 与调度折叠）────────────────────────────
  List<Widget> _buildToolLogs() {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final logs = _maps(_data?['tool_logs']).take(25).toList();
    final mcp = _maps(_data?['mcp_calls']).take(25).toList();
    final sched = _map(_data?['scheduler_trace']);
    final skipped = (sched['skipped_recent'] as num?)?.toInt() ?? 0;

    int ok = 0, partial = 0, failed = 0, blocked = 0;
    for (final l in logs) {
      switch (_classifyStatus(l['status'])) {
        case 'success':
          ok++;
          break;
        case 'partial':
          ok++;
          partial++;
          break;
        case 'failed':
          failed++;
          break;
        case 'blocked':
          blocked++;
          break;
      }
    }
    final attempts = ok + failed;
    final rate = attempts == 0 ? 0 : (ok * 100 / attempts).round();

    return [
      _sectionTitle(rs.t('agentMindToolLogs')),
      if (logs.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Text(
            '${rs.t('agentMindToolSuccess')} $ok（$partial ${rs.t('agentMindToolPartial')}）· '
            '${rs.t('agentMindToolFailed')} $failed · ${rs.t('agentMindToolBlocked')} $blocked · '
            '${rs.t('agentMindToolSkipped')} $skipped · $rate%',
            style: theme.textTheme.bodySmall,
          ),
        ),
      if (logs.isEmpty) _empty(rs.t('agentMindEmpty')),
      for (final l in logs)
        Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            dense: true,
            leading: Icon(_statusIcon(l['status']),
                size: 18, color: _statusColor(l['status'])),
            title: Text('${l['trigger'] ?? ''} / ${l['route'] ?? ''}'),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if ((l['steps'] as String?)?.isNotEmpty ?? false)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text('${l['steps']}',
                        maxLines: 3, overflow: TextOverflow.ellipsis),
                  ),
                Text('${_fmtTime(l['created_at'])} · ${l['latency_ms'] ?? 0}ms'),
              ],
            ),
          ),
        ),
      if (skipped > 0)
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Row(children: [
            const Icon(Icons.skip_next_outlined, size: 16, color: Colors.blueGrey),
            const SizedBox(width: 8),
            Expanded(
              child: Text('${rs.t('agentMindSkippedFold')} $skipped',
                  style: theme.textTheme.labelSmall),
            ),
          ]),
        ),
      const SizedBox(height: 10),
      _sectionTitle(rs.t('agentMindMcpCalls')),
      if (mcp.isEmpty)
        _empty(rs.t('agentMindMcpEmpty'))
      else
        for (final c in mcp)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              dense: true,
              leading: Icon(_statusIcon(c['status']),
                  size: 18, color: _statusColor(c['status'])),
              title: Text('${c['server_name'] ?? ''} / ${c['tool'] ?? ''}'),
              subtitle: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (((c['error'] as String?) ?? '').isNotEmpty)
                    Text('${c['error']}',
                        maxLines: 2, overflow: TextOverflow.ellipsis),
                  Text('${_fmtTime(c['created_at'])} · ${c['latency_ms'] ?? 0}ms'),
                ],
              ),
            ),
          ),
    ];
  }
}
