import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import 'marketplace_screen.dart';

/// 扩展（插件）页：分类列表 / 启用开关 / 参数配置 / zip 安装（仅主账号）
class ExtensionsScreen extends StatefulWidget {
  const ExtensionsScreen({super.key});

  @override
  State<ExtensionsScreen> createState() => _ExtensionsScreenState();
}

enum _PluginFilter { all, normal, mcp }

class _ExtensionsScreenState extends State<ExtensionsScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  String? _error;
  _PluginFilter _filter = _PluginFilter.all;
  List<Map<String, dynamic>> _plugins = [];

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().userId == 1;
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
      withData: false,
    );
    if (result == null || result.files.isEmpty) return;
    final path = result.files.single.path;
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
      switch (_filter) {
        case _PluginFilter.normal:
          return cat == 'plugin';
        case _PluginFilter.mcp:
          return cat == 'mcp';
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
            child: Container(
              padding: const EdgeInsets.all(2),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.55),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Row(children: [
                for (final (value, label) in [
                  (_PluginFilter.all, l10n.pluginAll),
                  (_PluginFilter.normal, l10n.pluginNormal),
                  (_PluginFilter.mcp, l10n.pluginMcp),
                ])
                  Expanded(
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onTap: () => setState(() => _filter = value),
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 180),
                        curve: Curves.easeOut,
                        padding: const EdgeInsets.symmetric(vertical: 7),
                        decoration: BoxDecoration(
                          color: _filter == value
                              ? Theme.of(context).colorScheme.surface
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(7),
                          boxShadow: _filter == value
                              ? [
                                  BoxShadow(
                                    color: Colors.black.withValues(alpha: 0.06),
                                    blurRadius: 2,
                                    offset: const Offset(0, 1),
                                  ),
                                ]
                              : null,
                        ),
                        child: Text(
                          label,
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontSize: 13,
                            fontWeight: _filter == value ? FontWeight.w600 : FontWeight.w400,
                            color: _filter == value
                                ? Theme.of(context).colorScheme.onSurface
                                : Theme.of(context).colorScheme.onSurfaceVariant,
                          ),
                        ),
                      ),
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
            FilledButton(onPressed: _load, child: const Text('重试')),
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
    final config = (p['config'] as Map<String, dynamic>?) ?? {};

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 8, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: category == 'mcp'
                        ? Theme.of(context).colorScheme.tertiaryContainer
                        : Theme.of(context).colorScheme.secondaryContainer,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Icon(
                    category == 'mcp' ? Icons.hub_outlined : Icons.extension_outlined,
                    size: 19,
                    color: Theme.of(context).colorScheme.onSecondaryContainer,
                  ),
                ),
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
                          Text('v$version', style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
                        ],
                      ),
                      if (description.isNotEmpty) ...[
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(description,
                              maxLines: _showDesc ? null : 2,
                              overflow: _showDesc ? null : TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 12, color: Color(0xFF6E6E73))),
                        ),
                        if (description.length > 50)
                          GestureDetector(
                            onTap: () => setState(() => _showDesc = !_showDesc),
                            child: Padding(
                              padding: const EdgeInsets.only(top: 2),
                              child: Text(
                                _showDesc ? '收起' : '展开全文',
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
                      color: category == 'mcp'
                          ? Theme.of(context).colorScheme.tertiaryContainer
                          : Theme.of(context).colorScheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(category == 'mcp' ? l10n.pluginMcp : l10n.pluginNormal,
                        style: const TextStyle(fontSize: 11)),
                  ),
                  if (author.isNotEmpty)
                    Text('${l10n.pluginAuthor}：$author', style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
                ],
              ),
            ),
            if (usage.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8),
                child: Row(
                  children: [
                    const Text('使用教程', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: () => setState(() => _showUsage = !_showUsage),
                      icon: Icon(_showUsage ? Icons.expand_less : Icons.expand_more, size: 16),
                      label: Text(_showUsage ? '收起' : '查看'),
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
                    Text(l10n.pluginConfig, style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93))),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: () => setState(() => _showConfig = !_showConfig),
                      icon: Icon(_showConfig ? Icons.expand_less : Icons.expand_more, size: 16),
                      label: Text(_showConfig ? '收起' : '展开'),
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('自定义设定', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        const Text('注入到 AI 抖音创作中（图文/回复生成时生效）',
            style: TextStyle(fontSize: 10, color: Colors.grey)),
        const SizedBox(height: 6),
        TextField(
          controller: _dyPromptCtrl,
          maxLines: 3,
          minLines: 2,
          enabled: !_dySaving,
          decoration: InputDecoration(
            hintText: '例如：发内容时多讲讲我们的故事，用温柔一点的语气…',
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
            child: const Text('保存设定', style: TextStyle(fontSize: 12)),
          ),
        ),
        const SizedBox(height: 2),
        const Text('待批准的抖音发布/回复请求请在「AI 好友」页右上角小信封查看',
            style: TextStyle(fontSize: 10, color: Colors.blueGrey)),
      ],
    );
  }

  Future<void> _saveDyCustomPrompt() async {
    if (_dySaving) return;
    setState(() => _dySaving = true);
    try {
      final cfg = Map<String, dynamic>.from(
          widget.plugin['config'] as Map<String, dynamic>? ?? {});
      cfg['custom_prompt'] = _dyPromptCtrl.text.trim();
      await ApiClient().updatePlugin('douyin_mcp', config: cfg);
      widget.onToast('自定义设定已保存');
      widget.onChanged();
    } catch (e) {
      widget.onToast('保存失败: $e');
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
          value: _selectValues[key],
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
