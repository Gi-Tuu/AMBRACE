// F7-c-4b（2026-08-31）自 features/plugin/mcp_tools_screen.dart 拆分迁入；逻辑逐字节保持。

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../theme/tokens.dart';

/// MCP 工具分区（Phase 3）：Server 管理（列表/状态灯/启停/连接/断开/测试/增删改）
/// + 工具列表（名称/描述/风险等级/权限三档切换）。
///
/// 状态灯数据来源：GET /servers 的实时 status（backend mcp_manager 从活动连接读取）。
/// Phase 4（2026-08-28）：状态灯实时刷新采用【轮询 GET /servers 兜底】——后端 mcp.server_status
/// 事件发布于进程内 EventBus，前端无对应全局事件总线/WebSocket 通道可订阅（notify_manager WS
/// 仅承载「新 AI 消息/主动消息」通知，未桥接 MCP 状态事件），故本页每 6s 轮询一次刷新状态灯；
/// 待后端将 MCP_SERVER_STATUS 桥接到用户级通知 WS 后可改为事件订阅。import 'mcp_server_widgets.dart' show McpStatusDot, McpTransportBadge;

class McpRecentCallTile extends StatelessWidget {
  const McpRecentCallTile({super.key, required this.call});
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

class McpToolTile extends StatelessWidget {
  const McpToolTile({super.key, 
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

// ------------------------------------------------------------------ 资源 / 提示词（Phase 4，只读展示）

/// 资源/提示词分区的节标题（小程序标题，复用扩展页风格）。
class McpResourceSectionLabel extends StatelessWidget {
  final String label;
  const McpResourceSectionLabel({super.key, required this.label});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(left: 24, top: 2, bottom: 6),
      child: Row(
        children: [
          Icon(Icons.folder_open_outlined, size: 14, color: Theme.of(context).colorScheme.secondary),
          const SizedBox(width: 6),
          Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

/// 资源条目：uri / name / 描述 / mimeType（只读，连接后可见）。
class McpResourceTile extends StatelessWidget {
  final Map<String, dynamic> resource;
  const McpResourceTile({super.key, required this.resource});

  @override
  Widget build(BuildContext context) {
    final uri = resource['uri'] as String? ?? '';
    final name = resource['name'] as String? ?? '';
    final desc = resource['description'] as String? ?? '';
    final mime = resource['mime_type'] as String? ?? '';
    final displayName = name.isNotEmpty ? name : uri;
    return Padding(
      padding: const EdgeInsets.only(left: 24, right: 4, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.description_outlined, size: 14, color: AppColors.textSecondary),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  displayName,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (mime.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                  decoration: BoxDecoration(
                    color: AppColors.textSecondary.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(mime,
                      style: const TextStyle(fontSize: 10, color: AppColors.textSecondary)),
                ),
            ],
          ),
          if (uri.isNotEmpty && name.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 1, left: 20),
              child: Text(uri,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
            ),
          if (desc.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, left: 20),
              child: Text(desc,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: AppColors.textMuted)),
            ),
        ],
      ),
    );
  }
}

/// 提示词条目：name / 描述 / 参数名列表（只读，连接后可见）。
class McpPromptTile extends StatelessWidget {
  final Map<String, dynamic> prompt;

  const McpPromptTile({super.key, required this.prompt});

  @override
  Widget build(BuildContext context) {
    final name = prompt['name'] as String? ?? '';
    final desc = prompt['description'] as String? ?? '';
    final args = prompt['arguments'];
    final argNames = (args is List)
        ? args.map((a) => (a is Map ? (a['name']?.toString() ?? '') : a?.toString() ?? '')).where((e) => e.isNotEmpty).toList()
        : <String>[];
    return Padding(
      padding: const EdgeInsets.only(left: 24, right: 4, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.forum_outlined, size: 14, color: AppColors.textSecondary),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  name,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          if (desc.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, left: 20),
              child: Text(desc,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: AppColors.textMuted)),
            ),
          if (argNames.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, left: 20),
              child: Text(argNames.join('、'),
                  style: const TextStyle(fontSize: 10, color: AppColors.textSecondary)),
            ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 添加/编辑表单

