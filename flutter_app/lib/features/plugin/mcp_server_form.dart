// F7-c-4b（2026-08-31）自 features/plugin/mcp_tools_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:convert';

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
/// 待后端将 MCP_SERVER_STATUS 桥接到用户级通知 WS 后可改为事件订阅。
class McpServerForm extends StatefulWidget {
  const McpServerForm({
    super.key,
    required this.server,
    required this.onSave,
  });

  final Map<String, dynamic>? server;
  final Future<Map<String, dynamic>> Function(Map<String, dynamic> body) onSave;

  @override
  State<McpServerForm> createState() => McpServerFormState();
}

class McpServerFormState extends State<McpServerForm> {
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
