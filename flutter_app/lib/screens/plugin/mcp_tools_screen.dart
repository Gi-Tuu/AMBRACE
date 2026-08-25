import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';

import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../theme/tokens.dart';

/// MCP 工具分区（Phase 3）：Server 管理（列表/状态灯/启停/连接/断开/测试/增删改）
/// + 工具列表（名称/描述/风险等级/权限三档切换）。
///
/// 状态灯数据来源：GET /servers 的实时 status（backend mcp_manager 从活动连接读取）。
/// Phase 4（2026-08-28）：状态灯实时刷新采用【轮询 GET /servers 兜底】——后端 mcp.server_status
/// 事件发布于进程内 EventBus，前端无对应全局事件总线/WebSocket 通道可订阅（notify_manager WS
/// 仅承载「新 AI 消息/主动消息」通知，未桥接 MCP 状态事件），故本页每 6s 轮询一次刷新状态灯；
/// 待后端将 MCP_SERVER_STATUS 桥接到用户级通知 WS 后可改为事件订阅。
class MCPToolsScreen extends StatefulWidget {
  const MCPToolsScreen({super.key});

  @override
  State<MCPToolsScreen> createState() => _MCPToolsScreenState();
}

class _MCPToolsScreenState extends State<MCPToolsScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  String? _error;
  List<Map<String, dynamic>> _servers = [];
  // 最近 MCP 工具调用（Phase 4，只读展示；GET /logs）
  List<Map<String, dynamic>> _recentCalls = [];
  // server_id -> tools（拉取后缓存，用于回显权限）
  final Map<int, List<Map<String, dynamic>>> _toolsCache = {};
  // 展开哪个 server 的工具列表
  int? _expandedServerId;
  // 状态灯轮询兜底计时器（见类注释：前端无 mcp.server_status 事件通道）
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().isAdmin;
    _load();
    _startRefreshTimer();
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  void _startRefreshTimer() {
    _refreshTimer?.cancel();
    // 6s 轮询刷新状态灯（轻量 GET /servers；与用户手动刷新一致，失败静默保留旧列表）
    _refreshTimer = Timer.periodic(const Duration(seconds: 6), (_) {
      _load(silent: true);
    });
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final items = await ApiClient().getMcpServers();
      final calls = await _loadMcpCallLogs();
      if (!mounted) return;
      setState(() {
        _servers = items;
        _recentCalls = calls;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (!silent) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  Future<List<Map<String, dynamic>>> _loadMcpCallLogs() async {
    try {
      return await ApiClient().getMcpCallLogs();
    } catch (_) {
      // 日志拉取失败不阻断主列表
      return [];
    }
  }

  String _errMsg(Object e) {
    final s = e.toString();
    return s.replaceFirst('DioException [bad response]: ', '');
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // ------------------------------------------------------------- 增删改
  Future<void> _openForm([Map<String, dynamic>? server]) async {
    final l10n = AppLocalizations.of(context)!;
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      builder: (_) => _McpServerForm(
        server: server,
        key: ValueKey('mcp_form_${server?['id'] ?? 'new'}'),
        onSave: server == null
            ? (body) => ApiClient().createMcpServer(body)
            : (body) => ApiClient().updateMcpServer(server['id'] as int, body),
      ),
    );
    if (saved == true) {
      _toast(l10n.mcpSaveSuccess);
      await _cleanupCache();
      await _load();
    }
  }

  Future<void> _delete(Map<String, dynamic> server) async {
    final l10n = AppLocalizations.of(context)!;
    final name = (server['name'] as String? ?? '');
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(l10n.mcpDeleteConfirmTitle),
        content: Text(l10n.mcpDeleteConfirmBody(name)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(l10n.mcpCancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(l10n.mcpDelete),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await ApiClient().deleteMcpServer(server['id'] as int);
      if (!mounted) return;
      _toast(l10n.mcpDeleteSuccess);
      await _cleanupCache();
      await _load();
    } catch (e) {
      _toast(l10n.mcpDeleteFail(_errMsg(e)));
    }
  }

  Future<void> _cleanupCache() async {
    final keys = _toolsCache.keys.toList();
    for (final k in keys) {
      _toolsCache.remove(k);
    }
  }

  // ------------------------------------------------------------- 连接/测试
  Future<void> _connect(Map<String, dynamic> server) async {
    final l10n = AppLocalizations.of(context)!;
    final id = server['id'] as int;
    try {
      await ApiClient().connectMcpServer(id);
      _toast(l10n.mcpConnectSuccess);
      await _load();
      if (_expandedServerId == id) await _loadToolsIfExpanded(id);
    } catch (e) {
      _toast(l10n.mcpConnectFail(_errMsg(e)));
    }
  }

  Future<void> _disconnect(Map<String, dynamic> server) async {
    final l10n = AppLocalizations.of(context)!;
    final id = server['id'] as int;
    try {
      await ApiClient().disconnectMcpServer(id);
      _toast(l10n.mcpDisconnectSuccess);
      await _load();
    } catch (e) {
      _toast(l10n.mcpConnectFail(_errMsg(e)));
    }
  }

  Future<void> _test(Map<String, dynamic> server) async {
    final l10n = AppLocalizations.of(context)!;
    final id = server['id'] as int;
    try {
      final r = await ApiClient().testMcpServer(id);
      final tools = (r['tools'] as List? ?? const []).length;
      _toast(l10n.mcpTestSuccess(tools));
    } catch (e) {
      _toast(l10n.mcpTestFail(_errMsg(e)));
    }
  }

  // ------------------------------------------------------------- 工具列表
  Future<void> _toggleTools(Map<String, dynamic> server) async {
    final id = server['id'] as int;
    if (_expandedServerId == id) {
      setState(() => _expandedServerId = null);
      return;
    }
    setState(() => _expandedServerId = id);
    await _loadTools(id);
  }

  Future<void> _loadToolsIfExpanded(int id) async {
    if (_expandedServerId == id) await _loadTools(id);
  }

  Future<void> _loadTools(int id) async {
    try {
      final tools = await ApiClient().getMcpServerTools(id);
      if (!mounted) return;
      setState(() => _toolsCache[id] = tools);
    } catch (_) {
      // 工具拉取失败不阻断主列表；保持原缓存
    }
  }

  Future<void> _setPermission(
    Map<String, dynamic> server,
    Map<String, dynamic> tool,
    String mode,
  ) async {
    final l10n = AppLocalizations.of(context)!;
    final id = server['id'] as int;
    final name = (tool['name'] as String? ?? '');
    try {
      await ApiClient().setMcpToolPermission(id, name, mode);
      if (!mounted) return;
      final tools = _toolsCache[id];
      if (tools != null) {
        for (final t in tools) {
          if (t['name'] == name) {
            t['mode'] = mode;
            break;
          }
        }
        setState(() {});
      }
      _toast(l10n.mcpPermissionSaved);
    } catch (e) {
      _toast(l10n.mcpPermissionSaveFail(_errMsg(e)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.mcpToolsTitle),
        actions: [
          IconButton(
            tooltip: l10n.mcpToolsRefresh,
            icon: const Icon(Icons.refresh),
            onPressed: _load,
          ),
          if (_isAdmin)
            IconButton(
              tooltip: l10n.mcpAddServer,
              icon: const Icon(Icons.add),
              onPressed: () => _openForm(),
            ),
        ],
      ),
      body: _buildBody(l10n),
    );
  }

  Widget _buildBody(AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(_error!, style: const TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 8),
            FilledButton(onPressed: _load, child: Text(l10n.mcpToolsRefresh)),
          ],
        ),
      );
    }
    if (_servers.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.hub_outlined, size: 48, color: Colors.grey.shade400),
            const SizedBox(height: 8),
            Text(l10n.mcpNoServers, style: TextStyle(color: Colors.grey.shade600)),
          ],
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
      itemCount: _servers.length + (_recentCalls.isNotEmpty ? 1 : 0),
      itemBuilder: (context, i) {
        // 末尾追加「最近调用」分区（Phase 4，只读展示）
        if (i >= _servers.length) {
          return _RecentCallsSection(
            calls: _recentCalls,
            onRefresh: _load,
          );
        }
        final server = _servers[i];
        final id = server['id'] as int;
        return _ServerCard(
          server: server,
          isAdmin: _isAdmin,
          expanded: _expandedServerId == id,
          tools: _toolsCache[id] ?? const [],
          loadingTools: _expandedServerId == id && _toolsCache[id] == null,
          onToggle: () => _toggleTools(server),
          onConnect: () => _connect(server),
          onDisconnect: () => _disconnect(server),
          onTest: () => _test(server),
          onEdit: () => _openForm(server),
          onDelete: () => _delete(server),
          onEnable: _isAdmin ? (v) => _updateEnabled(server, v) : null,
          onPermission: (tool, mode) => _setPermission(server, tool, mode),
          onRefreshTools: () => _loadTools(id),
          onToast: _toast,
        );
      },
    );
  }

  Future<void> _updateEnabled(
    Map<String, dynamic> server,
    bool enabled,
  ) async {
    final id = server['id'] as int;
    try {
      await ApiClient().updateMcpServer(id, {'enabled': enabled});
      await _load();
    } catch (e) {
      _toast(_errMsg(e));
    }
  }
}

// ------------------------------------------------------------------ Server 卡片

class _ServerCard extends StatelessWidget {
  const _ServerCard({
    required this.server,
    required this.isAdmin,
    required this.expanded,
    required this.tools,
    required this.loadingTools,
    required this.onToggle,
    required this.onConnect,
    required this.onDisconnect,
    required this.onTest,
    required this.onEdit,
    required this.onDelete,
    required this.onEnable,
    required this.onPermission,
    required this.onRefreshTools,
    required this.onToast,
  });

  final Map<String, dynamic> server;
  final bool isAdmin;
  final bool expanded;
  final List<Map<String, dynamic>> tools;
  final bool loadingTools;
  final VoidCallback onToggle;
  final VoidCallback onConnect;
  final VoidCallback onDisconnect;
  final VoidCallback onTest;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final void Function(bool)? onEnable;
  final void Function(Map<String, dynamic> tool, String mode) onPermission;
  final VoidCallback onRefreshTools;
  final void Function(String msg) onToast;

  Color _statusColor(String status) {
    switch (status) {
      case 'connected':
        return AppColors.success;
      case 'error':
        return AppColors.error;
      case 'connecting':
        return AppColors.warning;
      default:
        return AppColors.textTertiary;
    }
  }

  String _statusLabel(AppLocalizations l10n, String status) {
    switch (status) {
      case 'connected':
        return l10n.mcpStatusConnected;
      case 'error':
        return l10n.mcpStatusError;
      default:
        return l10n.mcpStatusDisconnected;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final name = server['name'] as String? ?? '';
    final status = server['status'] as String? ?? 'disconnected';
    final transport = server['transport'] as String? ?? 'stdio';
    final toolCount = (server['tools'] as num?)?.toInt() ?? 0;
    final enabled = server['enabled'] == true;
    final statusColor = _statusColor(status);

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                _StatusDot(color: statusColor),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        name,
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                        overflow: TextOverflow.ellipsis,
                      ),
                      Row(
                        children: [
                          _TransportBadge(transport: transport),
                          const SizedBox(width: 6),
                          Text(
                            _statusLabel(l10n, status),
                            style: TextStyle(fontSize: 11, color: statusColor == AppColors.textTertiary ? Colors.grey : statusColor),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                if (isAdmin)
                  IconButton(
                    tooltip: l10n.mcpEditServer,
                    icon: const Icon(Icons.edit_outlined, size: 19, color: Colors.grey),
                    onPressed: onEdit,
                  ),
                if (isAdmin)
                  IconButton(
                    tooltip: l10n.mcpDelete,
                    icon: const Icon(Icons.delete_outline, size: 20, color: Colors.grey),
                    onPressed: onDelete,
                  ),
                Switch(
                  value: enabled,
                  onChanged: onEnable,
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(left: 24),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Icon(Icons.widgets_outlined, size: 13, color: AppColors.textSecondary),
                  const SizedBox(width: 2),
                  Text(l10n.mcpToolsCount(toolCount),
                      style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                  const SizedBox(width: 6),
                  FilledButton.tonalIcon(
                    onPressed: onConnect,
                    icon: const Icon(Icons.play_arrow, size: 16),
                    label: Text(l10n.mcpConnect),
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                    ),
                  ),
                  const SizedBox(width: 4),
                  FilledButton.tonalIcon(
                    onPressed: onDisconnect,
                    icon: const Icon(Icons.stop, size: 16),
                    label: Text(l10n.mcpDisconnect),
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                    ),
                  ),
                  const SizedBox(width: 4),
                  TextButton.icon(
                    onPressed: onTest,
                    icon: const Icon(Icons.science_outlined, size: 16),
                    label: Text(l10n.mcpTest),
                    style: TextButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                    ),
                  ),
                  TextButton.icon(
                    onPressed: onToggle,
                    icon: Icon(expanded ? Icons.expand_less : Icons.expand_more, size: 16),
                    label: Text(l10n.mcpTools),
                    style: TextButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                    ),
                  ),
                ],
              ),
            ),
            if (expanded) ...[
              const Divider(height: 18),
              if (loadingTools)
                const Center(
                  child: Padding(
                    padding: EdgeInsets.all(10),
                    child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                  ),
                )
              else if (tools.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(left: 24, top: 4, bottom: 4),
                  child: Row(
                    children: [
                      Expanded(child: Text(l10n.mcpToolsEmpty, style: const TextStyle(fontSize: 12, color: Colors.grey))),
                      TextButton(
                        onPressed: onRefreshTools,
                        child: Text(l10n.mcpToolsRefresh),
                      ),
                    ],
                  ),
                )
              else
                ...tools.map((t) => _ToolTile(tool: t, isAdmin: isAdmin, onPermission: (m) => onPermission(t, m))),
            ],
          ],
        ),
      ),
    );
  }
}

class _StatusDot extends StatelessWidget {
  const _StatusDot({required this.color});
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 10,
      height: 10,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        boxShadow: [BoxShadow(color: color.withValues(alpha: 0.4), blurRadius: 4)],
      ),
    );
  }
}

class _TransportBadge extends StatelessWidget {
  const _TransportBadge({required this.transport});
  final String transport;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final label = switch (transport) {
      'sse' => l10n.mcpTransportSse,
      'streamable_http' => l10n.mcpTransportHttp,
      _ => l10n.mcpTransportStdio,
    };
    final color = switch (transport) {
      'sse' => const Color(0xFF00897B),
      'streamable_http' => const Color(0xFF7C4DFF),
      _ => Theme.of(context).colorScheme.secondary,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(label, style: TextStyle(fontSize: 10, color: color)),
    );
  }
}

// ------------------------------------------------------------------ 最近调用（Phase 4，只读展示）

/// 「最近 MCP 工具调用」分区（Phase 4）：GET /logs 轻量实现，只读展示。
///
/// 标题与状态文案沿用界面语言；工具名/参数摘要/耗时来自后端 mcp_call_logs。
class _RecentCallsSection extends StatelessWidget {
  const _RecentCallsSection({
    required this.calls,
    required this.onRefresh,
  });

  final List<Map<String, dynamic>> calls;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.history, size: 15, color: AppColors.textSecondary),
                const SizedBox(width: 6),
                Text(l10n.mcpRecentCalls,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                const Spacer(),
                TextButton(
                  onPressed: onRefresh,
                  style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
                  child: Text(l10n.mcpToolsRefresh),
                ),
              ],
            ),
            const Divider(height: 10),
            ...calls.map((c) => _RecentCallTile(call: c)),
          ],
        ),
      ),
    );
  }
}

class _RecentCallTile extends StatelessWidget {
  const _RecentCallTile({required this.call});
  final Map<String, dynamic> call;

  String _statusLabel(AppLocalizations l10n) {
    switch (call['status']) {
      case 'ok':
        return l10n.mcpCallOk;
      case 'timeout':
        return l10n.mcpCallTimeout;
      case 'blocked':
        return l10n.mcpCallBlocked;
      default:
        return l10n.mcpCallFailed;
    }
  }

  Color _statusColor() {
    if (call['ok'] == true) return AppColors.success;
    if (call['status'] == 'timeout') return AppColors.warning;
    return AppColors.error;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final tool = call['tool'] as String? ?? '';
    final server = call['server_name'] as String? ?? '';
    final args = call['arguments_summary'] as String? ?? '';
    final latency = (call['latency_ms'] as num?)?.toInt() ?? 0;
    final color = _statusColor();
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  tool,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              Text(_statusLabel(l10n),
                  style: TextStyle(fontSize: 10, color: color, fontWeight: FontWeight.w600)),
              const SizedBox(width: 6),
              Text('${latency}ms', style: const TextStyle(fontSize: 10, color: Colors.grey)),
            ],
          ),
          if (server.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 1, right: 4),
              child: Text(server,
                  style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
            ),
          if (args.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 1, right: 4),
              child: Text(args,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
            ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 工具项

class _ToolTile extends StatelessWidget {
  const _ToolTile({
    required this.tool,
    required this.isAdmin,
    required this.onPermission,
  });

  final Map<String, dynamic> tool;
  final bool isAdmin;
  final void Function(String mode) onPermission;

  String _riskLabel(AppLocalizations l10n) {
    switch (tool['risk_level']) {
      case 'high':
        return l10n.mcpRiskHigh;
      case 'low':
        return l10n.mcpRiskLow;
      default:
        return l10n.mcpRiskMedium;
    }
  }

  Color _riskColor() {
    switch (tool['risk_level']) {
      case 'high':
        return AppColors.error;
      case 'low':
        return AppColors.success;
      default:
        return AppColors.warning;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final name = tool['name'] as String? ?? '';
    final desc = tool['description'] as String? ?? '';
    final mode = tool['mode'] as String? ?? 'allow';
    return Padding(
      padding: const EdgeInsets.only(left: 24, right: 4, bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.build_outlined, size: 14, color: AppColors.textSecondary),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  name,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: _riskColor().withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(_riskLabel(l10n),
                    style: TextStyle(fontSize: 10, color: _riskColor())),
              ),
            ],
          ),
          if (desc.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, left: 20),
              child: Text(
                desc,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(top: 6, left: 20),
            child: Row(
              children: [
                Text(l10n.mcpPermissionLabel,
                    style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                const SizedBox(width: 8),
                Expanded(
                  child: SegmentedButton<String>(
                    segments: [
                      ButtonSegment(value: 'allow', label: Text(l10n.mcpPermissionAllow)),
                      ButtonSegment(value: 'ask', label: Text(l10n.mcpPermissionAsk)),
                      ButtonSegment(value: 'forbid', label: Text(l10n.mcpPermissionForbid)),
                    ],
                    selected: {mode},
                    showSelectedIcon: false,
                    style: ButtonStyle(
                      visualDensity: VisualDensity.compact,
                      textStyle: const WidgetStatePropertyAll(TextStyle(fontSize: 11)),
                    ),
                    onSelectionChanged: isAdmin
                        ? (sel) {
                            if (sel.isNotEmpty && sel.first != mode) onPermission(sel.first);
                          }
                        : null,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 添加/编辑表单

class _McpServerForm extends StatefulWidget {
  const _McpServerForm({
    super.key,
    required this.server,
    required this.onSave,
  });

  final Map<String, dynamic>? server;
  final Future<Map<String, dynamic>> Function(Map<String, dynamic> body) onSave;

  @override
  State<_McpServerForm> createState() => _McpServerFormState();
}

class _McpServerFormState extends State<_McpServerForm> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _commandCtrl;
  late final TextEditingController _argsCtrl;
  late final TextEditingController _envCtrl;
  late final TextEditingController _urlCtrl;
  late final TextEditingController _headersCtrl;
  late String _transport;
  late bool _enabled;
  late bool _autoConnect;
  bool _saving = false;
  bool _nameError = false;

  @override
  void initState() {
    super.initState();
    final s = widget.server;
    _nameCtrl = TextEditingController(text: (s?['name'] as String? ?? ''));
    _commandCtrl = TextEditingController(text: (s?['command'] as String? ?? ''));
    _argsCtrl = TextEditingController(text: _joinLines(s?['args'] as List?));
    _envCtrl = TextEditingController(text: _joinEnv(s?['env'] as Map?));
    _urlCtrl = TextEditingController(text: (s?['url'] as String? ?? ''));
    _headersCtrl = TextEditingController(text: _toJson(s?['headers'] as Map?));
    _transport = s?['transport'] as String? ?? 'stdio';
    _enabled = s?['enabled'] != false;
    _autoConnect = s?['auto_connect'] != false;
  }

  static String _joinLines(List? args) {
    if (args == null) return '';
    return args.map((e) => e.toString()).join('\n');
  }

  static String _joinEnv(Map? env) {
    if (env == null) return '';
    return env.entries.map((e) => '${e.key}=${e.value}').join('\n');
  }

  static String _toJson(Map? headers) {
    if (headers == null || headers.isEmpty) return '';
    return jsonEncode(headers);
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _commandCtrl.dispose();
    _argsCtrl.dispose();
    _envCtrl.dispose();
    _urlCtrl.dispose();
    _headersCtrl.dispose();
    super.dispose();
  }

  void _dump() {
    if (_nameCtrl.text.trim().isEmpty) {
      setState(() => _nameError = true);
      return;
    }
    final isStdio = _transport == 'stdio';
    final body = <String, dynamic>{
      'name': _nameCtrl.text.trim(),
      'transport': _transport,
      'enabled': _enabled,
      'auto_connect': _autoConnect,
    };
    if (isStdio) {
      body['command'] = _commandCtrl.text.trim();
      body['args'] = _argsCtrl.text
          .split('\n')
          .map((e) => e.trim())
          .where((e) => e.isNotEmpty)
          .toList();
      body['env'] = _parseEnv(_envCtrl.text);
    } else {
      body['url'] = _urlCtrl.text.trim();
      body['headers'] = _parseHeaders(_headersCtrl.text);
    }
    _save(body);
  }

  static Map<String, String> _parseEnv(String text) {
    final out = <String, String>{};
    for (final line in text.split('\n')) {
      final t = line.trim();
      if (t.isEmpty) continue;
      final idx = t.indexOf('=');
      if (idx > 0) out[t.substring(0, idx).trim()] = t.substring(idx + 1).trim();
    }
    return out;
  }

  static Map<String, String> _parseHeaders(String text) {
    final t = text.trim();
    if (t.isEmpty) return {};
    try {
      final d = jsonDecode(t);
      if (d is Map) {
        return d.map((k, v) => MapEntry(k.toString(), v.toString()));
      }
    } catch (_) {}
    // 退化为 KEY=value 每行解析
    return _parseEnv(text);
  }

  Future<void> _save(Map<String, dynamic> body) async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      await widget.onSave(body);
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      final s = e.toString().replaceFirst('DioException [bad response]: ', '');
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(s)));
    }
  }

  void _applyPreset(String kind) {
    final l10n = AppLocalizations.of(context)!;
    switch (kind) {
      case 'filesystem':
        _transport = 'stdio';
        _commandCtrl.text = 'npx';
        _argsCtrl.text = '-y\n@modelcontextprotocol/server-filesystem\n/path/to/dir';
        _addLog(SnackBar(content: Text('${l10n.mcpPresetFilesystem} → stdio')));
      case 'github':
        _transport = 'stdio';
        _commandCtrl.text = 'npx';
        _argsCtrl.text = '-y\n@modelcontextprotocol/server-github';
        _addLog(SnackBar(content: Text('${l10n.mcpPresetGithub} → stdio')));
      case 'sqlite':
        _transport = 'stdio';
        _commandCtrl.text = 'npx';
        _argsCtrl.text = '-y\n@modelcontextprotocol/server-sqlite\n/path/to/db.sqlite';
        _addLog(SnackBar(content: Text('${l10n.mcpPresetSqlite} → stdio')));
    }
    setState(() {});
  }

  void _addLog(SnackBar sb) {
    ScaffoldMessenger.of(context).showSnackBar(sb);
  }

  Widget _field(String label, TextEditingController ctrl,
      {int maxLines = 1, String? hint, bool autoFocus = false}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: TextField(
        controller: ctrl,
        maxLines: maxLines,
        minLines: maxLines > 1 ? 1 : null,
        autofocus: autoFocus,
        enabled: !_saving,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          isDense: true,
          alignLabelWithHint: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isStdio = _transport == 'stdio';
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              widget.server == null ? l10n.mcpAddServer : l10n.mcpEditServer,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: TextField(
                controller: _nameCtrl,
                autofocus: widget.server == null,
                enabled: !_saving,
                decoration: InputDecoration(
                  labelText: l10n.mcpName,
                  hintText: l10n.mcpNameHint,
                  isDense: true,
                  errorText: _nameError ? l10n.mcpNameRequired : null,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
                ),
              ),
            ),
            DropdownButtonFormField<String>(
              initialValue: _transport,
              decoration: InputDecoration(
                labelText: l10n.mcpTransportLabel,
                isDense: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
              ),
              items: [
                DropdownMenuItem(value: 'stdio', child: Text(l10n.mcpTransportStdio)),
                DropdownMenuItem(value: 'sse', child: Text(l10n.mcpTransportSse)),
                DropdownMenuItem(value: 'streamable_http', child: Text(l10n.mcpTransportHttp)),
              ],
              onChanged: _saving
                  ? null
                  : (v) => setState(() => _transport = v ?? 'stdio'),
            ),
            const SizedBox(height: 4),
            if (isStdio) ...[
              const SizedBox(height: 10),
              _field(l10n.mcpCommand, _commandCtrl),
              _field(l10n.mcpArgs, _argsCtrl, maxLines: 4),
              _field(l10n.mcpEnv, _envCtrl, maxLines: 3),
            ] else ...[
              const SizedBox(height: 10),
              _field(l10n.mcpUrl, _urlCtrl, hint: 'https://...'),
              _field(l10n.mcpHeaders, _headersCtrl, maxLines: 3),
            ],
            if (isStdio) ...[
              const SizedBox(height: 4),
              Text(l10n.mcpPreset, style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                children: [
                  ActionChip(
                    label: Text(l10n.mcpPresetFilesystem, style: const TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                    onPressed: _saving ? null : () => _applyPreset('filesystem'),
                  ),
                  ActionChip(
                    label: Text(l10n.mcpPresetGithub, style: const TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                    onPressed: _saving ? null : () => _applyPreset('github'),
                  ),
                  ActionChip(
                    label: Text(l10n.mcpPresetSqlite, style: const TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                    onPressed: _saving ? null : () => _applyPreset('sqlite'),
                  ),
                ],
              ),
            ],
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.mcpEnabled, style: const TextStyle(fontSize: 14)),
              value: _enabled,
              onChanged: _saving ? null : (v) => setState(() => _enabled = v),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.mcpAutoConnect, style: const TextStyle(fontSize: 14)),
              value: _autoConnect,
              onChanged: _saving ? null : (v) => setState(() => _autoConnect = v),
            ),
            const SizedBox(height: 4),
            Align(
              alignment: Alignment.centerRight,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextButton(
                    onPressed: _saving ? null : () => Navigator.of(context).pop(false),
                    child: Text(l10n.mcpCancel),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _saving ? null : _dump,
                    child: _saving
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : Text(l10n.mcpSave),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
