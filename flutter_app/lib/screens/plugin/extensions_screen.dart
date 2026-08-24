import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import 'marketplace_screen.dart';
import 'plugin_chat_screen.dart';
import 'plugin_webview_screen.dart';
import "package:ai_companion/theme/tokens.dart";

/// 扩展（插件）页：分类列表 / 启用开关 / 参数配置 / zip 安装（仅主账号）
class ExtensionsScreen extends StatefulWidget {
  const ExtensionsScreen({super.key});

  @override
  State<ExtensionsScreen> createState() => _ExtensionsScreenState();
}

enum _PluginFilter { all, normal, mcp, prompt, chat, workflow }

/// 48c/48a：type 徽标文案（http/prompt/chat/workflow/hybrid；mcp 沿用 category）
String _pluginTypeLabel(BuildContext context, String type, String category) {
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
Color _pluginTypeColor(BuildContext context, String type, String category) {
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
IconData _pluginTypeIcon(String type, String category) {
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
      itemBuilder: (context, i) => _PluginCard(
        plugin: filtered[i],
        isAdmin: _isAdmin,
        onChanged: _load,
        onToast: _toast,
      ),
    );
  }
}

class _PluginCard extends StatefulWidget {
  const _PluginCard({
    required this.plugin,
    required this.isAdmin,
    required this.onChanged,
    required this.onToast,
  });

  final Map<String, dynamic> plugin;
  final bool isAdmin;
  final VoidCallback onChanged;
  final void Function(String) onToast;

  @override
  State<_PluginCard> createState() => _PluginCardState();
}

class _PluginCardState extends State<_PluginCard> {
  late bool _enabled;
  bool _busy = false;
  bool _showConfig = false;
  bool _showDesc = false;
  bool _showUsage = false;
  // douyin_mcp 自定义设定（注入 AI 抖音创作；待批准请求统一在「AI 好友」小信封查看）
  final TextEditingController _dyPromptCtrl = TextEditingController();
  bool _dySaving = false;

  /// 48a：插件图标展示（manifest.icon 相对路径 → 页面托管 URL；加载失败回退 type 图标）
  Widget _iconWidget(String name, String type, String category, String icon) {
    final fallback = Container(
      width: 36,
      height: 36,
      decoration: BoxDecoration(
        color: _pluginTypeColor(context, type, category).withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Icon(
        _pluginTypeIcon(type, category),
        size: 19,
        color: _pluginTypeColor(context, type, category),
      ),
    );
    if (icon.isEmpty) return fallback;
    final url = icon.startsWith('http')
        ? icon
        : ApiClient().getPluginPageUrl(name, icon);
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: Image.network(
        url,
        width: 36,
        height: 36,
        fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => fallback,
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _enabled = widget.plugin['enabled'] as bool? ?? false;
    if (widget.plugin['name'] == 'douyin_mcp') {
      final cfg = widget.plugin['config'] as Map<String, dynamic>? ?? {};
      _dyPromptCtrl.text = (cfg['custom_prompt'] as String? ?? '');
    }
  }

  @override
  void dispose() {
    _dyPromptCtrl.dispose();
    super.dispose();
  }

  Future<void> _toggle(bool value) async {
    if (!widget.isAdmin || _busy) return;
    setState(() => _busy = true);
    try {
      await ApiClient().updatePlugin(widget.plugin['name'] as String, enabled: value);
      if (!mounted) return;
      setState(() => _enabled = value);
      widget.onToast(value
          ? AppLocalizations.of(context)!.pluginEnabledToast
          : AppLocalizations.of(context)!.pluginDisabledToast);
      widget.onChanged();
    } catch (e) {
      widget.onToast(e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 48a：卸载插件（仅主账号；二次确认；内置插件后端会拒绝）
  Future<void> _confirmUninstall() async {
    if (!widget.isAdmin || _busy) return;
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(l10n.pluginUninstall),
        content: Text(l10n.pluginUninstallConfirm),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(l10n.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(l10n.pluginUninstall),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await ApiClient().uninstallPlugin(widget.plugin['name'] as String);
      if (!mounted) return;
      widget.onToast(l10n.pluginUninstallSuccess);
      widget.onChanged();
    } catch (e) {
      final msg = e.toString().replaceFirst('DioException [bad response]: ', '');
      widget.onToast('${l10n.pluginUninstallFail}: $msg');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final p = widget.plugin;
    final name = p['name'] as String? ?? '';
    final version = p['version'] as String? ?? '';
    final description = p['description'] as String? ?? '';
    final usage = p['usage'] as String? ?? '';
    final author = p['author'] as String? ?? '';
    final category = p['category'] as String? ?? 'plugin';
    final type = p['type'] as String? ?? (category == 'mcp' ? 'mcp' : 'http');
    final config = (p['config'] as Map<String, dynamic>?) ?? {};
    final icon = p['icon'] as String? ?? '';
    final hasPage = p['has_page'] == true;

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 8, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                _iconWidget(name, type, category, icon),
                const SizedBox(width: 11),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(name, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                          ),
                          const SizedBox(width: 6),
                          Text('v$version', style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                        ],
                      ),
                      if (description.isNotEmpty) ...[
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(description,
                              maxLines: _showDesc ? null : 2,
                              overflow: _showDesc ? null : TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 12, color: AppColors.textMuted)),
                        ),
                        if (description.length > 50)
                          GestureDetector(
                            onTap: () => setState(() => _showDesc = !_showDesc),
                            child: Padding(
                              padding: const EdgeInsets.only(top: 2),
                              child: Text(
                                _showDesc ? l10n.extCollapse : l10n.extExpandFull,
                                style: TextStyle(
                                  fontSize: 11,
                                  color: Theme.of(context).colorScheme.primary,
                                ),
                              ),
                            ),
                          ),
                      ],
                    ],
                  ),
                ),
                if (widget.isAdmin)
                  IconButton(
                    tooltip: l10n.pluginUninstall,
                    icon: const Icon(Icons.delete_outline, size: 20, color: Colors.grey),
                    onPressed: _confirmUninstall,
                  ),
                Switch(
                  value: _enabled,
                  onChanged: widget.isAdmin ? _toggle : null,
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(left: 42, right: 8),
              child: Wrap(
                spacing: 6,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                    decoration: BoxDecoration(
                      color: _pluginTypeColor(context, type, category).withValues(alpha: 0.18),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(_pluginTypeLabel(context, type, category),
                        style: TextStyle(fontSize: 11, color: _pluginTypeColor(context, type, category))),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                    decoration: BoxDecoration(
                      color: category == 'mcp'
                          ? Theme.of(context).colorScheme.tertiaryContainer
                          : Theme.of(context).colorScheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(category == 'mcp' ? l10n.pluginMcp : l10n.pluginNormal,
                        style: const TextStyle(fontSize: 11)),
                  ),
                  if (author.isNotEmpty)
                    Text('${l10n.pluginAuthor}：$author', style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                ],
              ),
            ),
            if (usage.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8),
                child: Row(
                  children: [
                    Text(l10n.extUsageGuide, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: () => setState(() => _showUsage = !_showUsage),
                      icon: Icon(_showUsage ? Icons.expand_less : Icons.expand_more, size: 16),
                      label: Text(_showUsage ? l10n.extCollapse : l10n.extView),
                      style: TextButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                      ),
                    ),
                  ],
                ),
              ),
            if (_showUsage && usage.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 42, right: 8, bottom: 6),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(usage,
                      style: const TextStyle(fontSize: 12, height: 1.5, color: Color(0xFF3A3A3C))),
                ),
              ),
            if (config.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 6),
                child: Row(
                  children: [
                    Text(l10n.pluginConfig, style: const TextStyle(fontSize: 12, color: AppColors.textSecondary)),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: () => setState(() => _showConfig = !_showConfig),
                      icon: Icon(_showConfig ? Icons.expand_less : Icons.expand_more, size: 16),
                      label: Text(_showConfig ? l10n.extCollapse : l10n.extExpand),
                      style: TextButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                      ),
                    ),
                  ],
                ),
              ),
            if (_showConfig && config.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 42, right: 8),
                child: _PluginConfigForm(
                  key: ValueKey('cfg_$name'),
                  config: config,
                  isAdmin: widget.isAdmin,
                  onSaved: (values) => _saveConfig(values),
                  onToast: widget.onToast,
                ),
              ),
            if (type == 'chat')
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton.tonalIcon(
                    onPressed: () {
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PluginChatScreen(plugin: widget.plugin),
                      ));
                    },
                    icon: const Icon(Icons.chat_bubble_outline, size: 16),
                    label: Text(l10n.pluginOpen),
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                    ),
                  ),
                ),
              ),
            // 48a：has_page 页面型插件「打开」→ PluginWebviewScreen（区别于 chat 型「打开」）
            if (hasPage)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton.tonalIcon(
                    onPressed: () {
                      final page = widget.plugin['page'] as String? ?? 'index.html';
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PluginWebviewScreen(
                          pluginName: name,
                          pageUrl: ApiClient().getPluginPageUrl(name, page),
                        ),
                      ));
                    },
                    icon: const Icon(Icons.open_in_browser_outlined, size: 16),
                    label: Text(l10n.pluginOpenPage),
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                    ),
                  ),
                ),
              ),
            // 48c：prompt/chat 型零代码配置编辑器（仅主账号）
            if (type == 'prompt' || type == 'chat')
              if (widget.isAdmin)
                Padding(
                  padding: const EdgeInsets.only(left: 42, top: 10, right: 8),
                  child: _ZeroCodeConfigEditor(
                    key: ValueKey('zc_$name'),
                    plugin: widget.plugin,
                    onToast: widget.onToast,
                    onSaved: widget.onChanged,
                  ),
                ),
            if (name == 'douyin_mcp' && _enabled)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8, right: 8),
                child: _buildDyCreator(),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildDyCreator() {
    final l10n = AppLocalizations.of(context)!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(l10n.extCustomConfig, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        Text(l10n.extDoyinInjectHint,
            style: TextStyle(fontSize: 10, color: Colors.grey)),
        const SizedBox(height: 6),
        TextField(
          controller: _dyPromptCtrl,
          maxLines: 3,
          minLines: 2,
          enabled: !_dySaving,
          decoration: InputDecoration(
            hintText: l10n.extConfigExampleHint,
            isDense: true,
            border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
            contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          ),
        ),
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerRight,
          child: FilledButton.tonal(
            onPressed: _dySaving ? null : _saveDyCustomPrompt,
            style: FilledButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 14),
            ),
            child: Text(l10n.extSaveConfig, style: const TextStyle(fontSize: 12)),
          ),
        ),
        const SizedBox(height: 2),
        Text(l10n.extPendingHint,
            style: TextStyle(fontSize: 10, color: Colors.blueGrey)),
      ],
    );
  }

  Future<void> _saveDyCustomPrompt() async {
    final l10n = AppLocalizations.of(context)!;
    if (_dySaving) return;
    setState(() => _dySaving = true);
    try {
      final cfg = Map<String, dynamic>.from(
          widget.plugin['config'] as Map<String, dynamic>? ?? {});
      cfg['custom_prompt'] = _dyPromptCtrl.text.trim();
      await ApiClient().updatePlugin('douyin_mcp', config: cfg);
      widget.onToast(l10n.extConfigSaved);
      widget.onChanged();
    } catch (e) {
      widget.onToast(l10n.extSaveFailed('$e'));
    } finally {
      if (mounted) setState(() => _dySaving = false);
    }
  }

  Future<void> _saveConfig(Map<String, dynamic> values) async {
    if (!widget.isAdmin || _busy) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      await ApiClient().updatePlugin(widget.plugin['name'] as String, config: values);
      widget.onToast(l10n.pluginConfigSaved);
      widget.onChanged();
    } catch (e) {
      widget.onToast(e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

/// 按 manifest config 默认值类型生成表单：bool→开关 / num→数字框 / String→文本框 / List→下拉
class _PluginConfigForm extends StatefulWidget {
  const _PluginConfigForm({
    super.key,
    required this.config,
    required this.isAdmin,
    required this.onSaved,
    required this.onToast,
  });

  final Map<String, dynamic> config;
  final bool isAdmin;
  final void Function(Map<String, dynamic>) onSaved;
  final void Function(String) onToast;

  @override
  State<_PluginConfigForm> createState() => _PluginConfigFormState();
}

class _PluginConfigFormState extends State<_PluginConfigForm> {
  final Map<String, TextEditingController> _controllers = {};
  final Map<String, bool> _bools = {};
  final Map<String, List<String>> _selects = {};
  final Map<String, String> _selectValues = {};

  @override
  void initState() {
    super.initState();
    widget.config.forEach((key, value) {
      if (value is bool) {
        _bools[key] = value;
      } else if (value is num) {
        _controllers[key] = TextEditingController(text: value.toString());
      } else if (value is String) {
        _controllers[key] = TextEditingController(text: value);
      } else if (value is List) {
        final items = value.map((e) => e.toString()).toList();
        _selects[key] = items;
        _selectValues[key] = items.isNotEmpty ? items.first : '';
      }
    });
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[];
    _controllers.forEach((key, c) {
      final isNum = widget.config[key] is num;
      children.add(TextFormField(
        controller: c,
        enabled: widget.isAdmin,
        keyboardType: isNum ? TextInputType.number : TextInputType.text,
        decoration: InputDecoration(
          labelText: key,
          isDense: true,
          border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        ),
        onChanged: (_) => setState(() {}),
      ));
      children.add(const SizedBox(height: 8));
    });
    _bools.forEach((key, v) {
      children.add(SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(key, style: const TextStyle(fontSize: 14)),
        value: v,
        onChanged: widget.isAdmin
            ? (val) => setState(() => _bools[key] = val)
            : null,
      ));
    });
    _selects.forEach((key, items) {
      children.add(Padding(
        padding: const EdgeInsets.only(top: 4),
        child: DropdownButtonFormField<String>(
          initialValue: _selectValues[key],
          isDense: true,
          decoration: InputDecoration(
            labelText: key,
            isDense: true,
            border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
            contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          ),
          items: items.map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(),
          onChanged: widget.isAdmin
              ? (val) => setState(() => _selectValues[key] = val ?? items.first)
              : null,
        ),
      ));
      children.add(const SizedBox(height: 8));
    });

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ...children,
        if (widget.isAdmin)
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              onPressed: () {
                final values = <String, dynamic>{};
                _controllers.forEach((key, c) {
                  final raw = c.text.trim();
                  final isNum = widget.config[key] is num;
                  values[key] = isNum ? (num.tryParse(raw) ?? raw) : raw;
                });
                _bools.forEach((key, v) => values[key] = v);
                _selects.forEach((key, items) => values[key] = _selectValues[key] ?? items.first);
                widget.onSaved(values);
              },
              child: Text(AppLocalizations.of(context)!.pluginSaveConfig),
            ),
          )
        else
          Text(AppLocalizations.of(context)!.pluginNotWritable,
              style: const TextStyle(fontSize: 11, color: Colors.grey)),
      ],
    );
  }
}

/// 48c：prompt/chat 型零代码配置编辑器（仅主账号展示）——
/// prompt：触发词列表 + systemPrompt；chat：名称 + persona + greeting
class _ZeroCodeConfigEditor extends StatefulWidget {
  const _ZeroCodeConfigEditor({
    super.key,
    required this.plugin,
    required this.onToast,
    required this.onSaved,
  });

  final Map<String, dynamic> plugin;
  final void Function(String) onToast;
  final VoidCallback onSaved;

  @override
  State<_ZeroCodeConfigEditor> createState() => _ZeroCodeConfigEditorState();
}

class _ZeroCodeConfigEditorState extends State<_ZeroCodeConfigEditor> {
  late final String _type;
  late final TextEditingController _triggerCtrl;
  late final TextEditingController _systemPromptCtrl;
  late final TextEditingController _chatNameCtrl;
  late final TextEditingController _personaCtrl;
  late final TextEditingController _greetingCtrl;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _type = widget.plugin['type'] as String? ?? 'http';
    final config = (widget.plugin['config'] as Map<String, dynamic>?) ?? {};
    final prompt = (config['prompt'] as Map<String, dynamic>?) ?? {};
    final chat = (config['chat'] as Map<String, dynamic>?) ?? {};
    _triggerCtrl = TextEditingController(
        text: ((prompt['trigger'] as List?) ?? const []).map((e) => e.toString()).join('，'));
    _systemPromptCtrl = TextEditingController(text: (prompt['systemPrompt'] as String? ?? ''));
    _chatNameCtrl = TextEditingController(text: (chat['name'] as String? ?? ''));
    _personaCtrl = TextEditingController(text: (chat['persona'] as String? ?? ''));
    _greetingCtrl = TextEditingController(text: (chat['greeting'] as String? ?? ''));
  }

  @override
  void dispose() {
    _triggerCtrl.dispose();
    _systemPromptCtrl.dispose();
    _chatNameCtrl.dispose();
    _personaCtrl.dispose();
    _greetingCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_saving) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _saving = true);
    try {
      final config = Map<String, dynamic>.from(
          widget.plugin['config'] as Map<String, dynamic>? ?? {});
      if (_type == 'prompt') {
        config['prompt'] = {
          'trigger': _triggerCtrl.text
              .split(RegExp(r'[,，、;；]'))
              .map((e) => e.trim())
              .where((e) => e.isNotEmpty)
              .toList(),
          'systemPrompt': _systemPromptCtrl.text.trim(),
        };
      } else if (_type == 'chat') {
        config['chat'] = {
          if (_chatNameCtrl.text.trim().isNotEmpty) 'name': _chatNameCtrl.text.trim(),
          'persona': _personaCtrl.text.trim(),
          if (_greetingCtrl.text.trim().isNotEmpty) 'greeting': _greetingCtrl.text.trim(),
        };
      }
      await ApiClient().updatePlugin(widget.plugin['name'] as String, config: config);
      widget.onToast(l10n.pluginConfigSaved);
      widget.onSaved();
    } catch (e) {
      widget.onToast(e.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _field(String label, TextEditingController ctrl,
      {int maxLines = 3, String? hint}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: TextField(
        controller: ctrl,
        maxLines: maxLines,
        minLines: 1,
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
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isPrompt = _type == 'prompt';
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.pluginZeroCodeConfig,
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          if (isPrompt) ...[
            _field(l10n.pluginConfigTriggers, _triggerCtrl,
                maxLines: 2, hint: l10n.extHintWrite),
            _field(l10n.pluginConfigSystemPrompt, _systemPromptCtrl,
                maxLines: 6, hint: l10n.extHintWriter),
          ] else ...[
            _field(l10n.pluginConfigChatName, _chatNameCtrl, maxLines: 1),
            _field(l10n.pluginConfigPersona, _personaCtrl,
                maxLines: 6, hint: l10n.extHintDiary),
            _field(l10n.pluginConfigGreeting, _greetingCtrl,
                maxLines: 2, hint: l10n.extHintGreeting),
          ],
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              onPressed: _saving ? null : _save,
              style: FilledButton.styleFrom(
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 14),
              ),
              child: Text(l10n.pluginSaveConfig, style: const TextStyle(fontSize: 12)),
            ),
          ),
        ],
      ),
    );
  }
}
