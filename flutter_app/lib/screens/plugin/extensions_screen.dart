import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import 'marketplace_screen.dart';
import 'mcp_tools_screen.dart';
import '../../features/plugin/plugin_card.dart';

/// 扩展（插件）页：分类列表 / 启用开关 / 参数配置 / zip 安装（仅主账号）
class ExtensionsScreen extends StatefulWidget {
  const ExtensionsScreen({super.key});

  @override
  State<ExtensionsScreen> createState() => _ExtensionsScreenState();
}

enum _PluginFilter { all, normal, mcp, prompt, chat, workflow }

/// 48c/48a：type 徽标文案（http/prompt/chat/workflow/hybrid；mcp 沿用 category）
String pluginTypeLabel(BuildContext context, String type, String category) {
  final l10n = AppLocalizations.of(context)!;
  if (category == 'mcp') return l10n.pluginMcp;
  switch (type) {
    case 'prompt':
      return l10n.pluginTypePrompt;
    case 'chat':
      return l10n.pluginTypeChat;
    case 'workflow':
      return l10n.pluginTypeWorkflow;
    case 'hybrid':
      return l10n.pluginTypeHybrid;
    default:
      return l10n.pluginTypeHttp;
  }
}

/// 48c/48a：type 徽标颜色（prompt 紫 / chat 青 / workflow 橙 / hybrid 暖橘；mcp 用主题 tertiary）
Color pluginTypeColor(BuildContext context, String type, String category) {
  if (category == 'mcp') return Theme.of(context).colorScheme.tertiary;
  switch (type) {
    case 'prompt':
      return const Color(0xFF7C4DFF);
    case 'chat':
      return const Color(0xFF00897B);
    case 'workflow':
      return const Color(0xFFF57C00);
    case 'hybrid':
      return const Color(0xFFE8846C);
    default:
      return Theme.of(context).colorScheme.secondary;
  }
}

/// 48c/48a：type 图标（默认 extension；prompt auto_awesome / chat chat / workflow account_tree / hybrid web_asset）
IconData pluginTypeIcon(String type, String category) {
  if (category == 'mcp') return Icons.hub_outlined;
  switch (type) {
    case 'prompt':
      return Icons.auto_awesome_outlined;
    case 'chat':
      return Icons.chat_bubble_outline;
    case 'workflow':
      return Icons.account_tree_outlined;
    case 'hybrid':
      return Icons.web_asset_outlined;
    default:
      return Icons.extension_outlined;
  }
}

class _ExtensionsScreenState extends State<ExtensionsScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  String? _error;
  _PluginFilter _filter = _PluginFilter.all;
  List<Map<String, dynamic>> _plugins = [];

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().isAdmin;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await ApiClient().getPlugins();
      if (!mounted) return;
      setState(() {
        _plugins = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _installZip() async {
    final l10n = AppLocalizations.of(context)!;
    final result = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['zip'],
      // withData 已弃用（默认不读字节），移除
    );
    if (result.isEmpty) return;
    final path = result.single.path;
    if (path == null) {
      _toast(l10n.pluginNeedZip);
      return;
    }
    try {
      final plugin = await ApiClient().installPluginZip(path);
      if (!mounted) return;
      _toast('${l10n.pluginInstallSuccess}（${plugin['name']}）');
      await _load();
    } catch (e) {
      _toast('${l10n.pluginInstallFail}: ${_errMsg(e)}');
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final filtered = _plugins.where((p) {
      final cat = p['category'] as String? ?? 'plugin';
      final type = p['type'] as String? ?? (cat == 'mcp' ? 'mcp' : 'http');
      switch (_filter) {
        case _PluginFilter.normal:
          return cat == 'plugin';
        case _PluginFilter.mcp:
          return cat == 'mcp';
        case _PluginFilter.prompt:
          return cat == 'plugin' && type == 'prompt';
        case _PluginFilter.chat:
          return cat == 'plugin' && type == 'chat';
        case _PluginFilter.workflow:
          return cat == 'plugin' && type == 'workflow';
        case _PluginFilter.all:
          return true;
      }
    }).toList();

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.extensions),
        actions: [
          IconButton(
            tooltip: l10n.mcpToolsTitle,
            icon: const Icon(Icons.hub_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const MCPToolsScreen()),
            ),
          ),
          IconButton(
            tooltip: l10n.marketplace,
            icon: const Icon(Icons.storefront_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const MarketplaceScreen()),
            ),
          ),
          if (_isAdmin)
            IconButton(
              tooltip: l10n.pluginInstallZip,
              icon: const Icon(Icons.file_upload_outlined),
              onPressed: _installZip,
            ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: [
                for (final (value, label) in [
                  (_PluginFilter.all, l10n.pluginAll),
                  (_PluginFilter.normal, l10n.pluginNormal),
                  (_PluginFilter.mcp, l10n.pluginMcp),
                  (_PluginFilter.prompt, l10n.pluginTypePrompt),
                  (_PluginFilter.chat, l10n.pluginTypeChat),
                  (_PluginFilter.workflow, l10n.pluginTypeWorkflow),
                ])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(label, style: const TextStyle(fontSize: 12)),
                      selected: _filter == value,
                      onSelected: (_) => setState(() => _filter = value),
                      visualDensity: VisualDensity.compact,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                  ),
              ]),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.errorContainer.withValues(alpha: 0.35),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.warning_amber_rounded, size: 18, color: Theme.of(context).colorScheme.error),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '${l10n.pluginRiskTitle}：${l10n.pluginRiskHint}',
                      style: const TextStyle(fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (!_isAdmin)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Row(
                children: [
                  const Icon(Icons.lock_outline, size: 14, color: Colors.grey),
                  const SizedBox(width: 6),
                  Text(l10n.pluginOnlyAdmin, style: const TextStyle(fontSize: 12, color: Colors.grey)),
                ],
              ),
            ),
          Expanded(child: _buildBody(l10n, filtered)),
        ],
      ),
    );
  }

  Widget _buildBody(AppLocalizations l10n, List<Map<String, dynamic>> filtered) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(_error!, style: const TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 8),
            FilledButton(onPressed: _load, child: Text(l10n.extRetry)),
          ],
        ),
      );
    }
    if (filtered.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.extension_off_outlined, size: 48, color: Colors.grey.shade400),
            const SizedBox(height: 8),
            Text(l10n.pluginNoPlugins, style: TextStyle(color: Colors.grey.shade600)),
          ],
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
      itemCount: filtered.length,
      itemBuilder: (context, i) => PluginCard(
        plugin: filtered[i],
        isAdmin: _isAdmin,
        onChanged: _load,
        onToast: _toast,
      ),
    );
  }
}

