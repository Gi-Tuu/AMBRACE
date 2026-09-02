import 'dart:async';

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';

import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../features/plugin/mcp_server_widgets.dart';
import '../../features/plugin/mcp_server_form.dart';

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
  // server_id -> resources（Phase 4 只读展示，连接后可见）
  final Map<int, List<Map<String, dynamic>>> _resourcesCache = {};
  // server_id -> prompts（Phase 4 只读展示，连接后可见）
  final Map<int, List<Map<String, dynamic>>> _promptsCache = {};
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
      builder: (_) => McpServerForm(
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
      _resourcesCache.remove(k);
      _promptsCache.remove(k);
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
    // A2（#59）资源/提示词：
    // 资源/提示词拉取失败不阻断主列表；保持原缓存（后端未连接时返回空列表）。
    await _loadResources(id);
    await _loadPrompts(id);
  }

  /// A2（#59）：拉取该 Server 的资源列表（只读展示，连接后可见）。
  Future<void> _loadResources(int id) async {
    try {
      final resources = await ApiClient().getMcpServerResources(id);
      if (!mounted) return;
      setState(() => _resourcesCache[id] = resources);
    } catch (_) {
      // 失败不阻断；保持原缓存
    }
  }

  /// A2（#59）：拉取该 Server 的提示词列表（只读展示，连接后可见）。
  Future<void> _loadPrompts(int id) async {
    try {
      final prompts = await ApiClient().getMcpServerPrompts(id);
      if (!mounted) return;
      setState(() => _promptsCache[id] = prompts);
    } catch (_) {
      // 失败不阻断；保持原缓存
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
          return McpRecentCallsSection(
            calls: _recentCalls,
            onRefresh: _load,
          );
        }
        final server = _servers[i];
        final id = server['id'] as int;
        return McpServerCard(
          server: server,
          isAdmin: _isAdmin,
          expanded: _expandedServerId == id,
          tools: _toolsCache[id] ?? const [],
          resources: _resourcesCache[id] ?? const [],
          prompts: _promptsCache[id] ?? const [],
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

