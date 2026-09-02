// F7-c-4b（2026-08-31）自 features/plugin/mcp_tools_screen.dart 拆分迁入；逻辑逐字节保持。

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../theme/tokens.dart';
import 'mcp_tiles.dart' show McpToolTile, McpResourceSectionLabel, McpResourceTile, McpPromptTile, McpRecentCallTile;

/// MCP 工具分区（Phase 3）：Server 管理（列表/状态灯/启停/连接/断开/测试/增删改）
/// + 工具列表（名称/描述/风险等级/权限三档切换）。
///
/// 状态灯数据来源：GET /servers 的实时 status（backend mcp_manager 从活动连接读取）。
/// Phase 4（2026-08-28）：状态灯实时刷新采用【轮询 GET /servers 兜底】——后端 mcp.server_status
/// 事件发布于进程内 EventBus，前端无对应全局事件总线/WebSocket 通道可订阅（notify_manager WS
/// 仅承载「新 AI 消息/主动消息」通知，未桥接 MCP 状态事件），故本页每 6s 轮询一次刷新状态灯；
/// 待后端将 MCP_SERVER_STATUS 桥接到用户级通知 WS 后可改为事件订阅。
class McpServerCard extends StatelessWidget {
  const McpServerCard({super.key, 
    required this.server,
    required this.isAdmin,
    required this.expanded,
    required this.tools,
    required this.resources,
    required this.prompts,
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
  final List<Map<String, dynamic>> resources;
  final List<Map<String, dynamic>> prompts;
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
                McpStatusDot(color: statusColor),
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
                          McpTransportBadge(transport: transport),
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
                ...tools.map((t) => McpToolTile(tool: t, isAdmin: isAdmin, onPermission: (m) => onPermission(t, m))),
              // A2（#59）资源 / 提示词「只读」列表（Phase 4）：连接后可见；uri/name/描述、name/描述/参数。
              if (resources.isNotEmpty) ...[
                const Divider(height: 18),
                McpResourceSectionLabel(label: l10n.mcpResources),
                ...resources.map((r) => McpResourceTile(resource: r)),
              ],
              if (prompts.isNotEmpty) ...[
                const Divider(height: 18),
                McpResourceSectionLabel(label: l10n.mcpPrompts),
                ...prompts.map((p) => McpPromptTile(prompt: p)),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class McpStatusDot extends StatelessWidget {
  const McpStatusDot({super.key, required this.color});
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

class McpTransportBadge extends StatelessWidget {
  const McpTransportBadge({super.key, required this.transport});
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
class McpRecentCallsSection extends StatelessWidget {
  const McpRecentCallsSection({super.key, 
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
            ...calls.map((c) => McpRecentCallTile(call: c)),
          ],
        ),
      ),
    );
  }
}

