// F7-c-4c（2026-08-31）自 features/plugin/extensions_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../models/character.dart';
import 'plugin_chat_screen.dart';
import 'plugin_webview_screen.dart';
import "package:ai_companion/theme/tokens.dart";
import 'extensions_screen.dart' show pluginTypeLabel, pluginTypeColor, pluginTypeIcon;
import 'plugin_forms.dart' show PluginConfigForm, ZeroCodeConfigEditor;

/// 来源徽标文案（3.9）：builtin/remote/local -> 本地化
String _sourceLabel(AppLocalizations l10n, String source) {
  switch (source) {
    case 'remote':
      return l10n.pluginSourceRemote;
    case 'local':
      return l10n.pluginSourceLocal;
    case 'builtin':
      return l10n.pluginSourceBuiltin;
    default:
      return l10n.pluginSource;
  }
}

/// sha256 短值展示（前 10 位 + 省略号）
String _shaShort(String sha) => sha.length > 10 ? '${sha.substring(0, 10)}…' : sha;

/// 扩展（插件）页：分类列表 / 启用开关 / 参数配置 / zip 安装（仅主账号）
class PluginCard extends StatefulWidget {  const PluginCard({super.key, 
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
  State<PluginCard> createState() => PluginCardState();
}

class PluginCardState extends State<PluginCard> {
  late bool _enabled;
  bool _busy = false;
  bool _showConfig = false;
  bool _showDesc = false;
  bool _showUsage = false;
  // douyin_mcp 自定义设定（注入 AI 抖音创作；待批准请求统一在「AI 好友」小信封查看）
  final TextEditingController _dyPromptCtrl = TextEditingController();
  bool _dySaving = false;
  // douyin_mcp 绑定角色（#68 P5 组级唯一；-1=未绑定）
  int _dyBoundCharId = -1;
  bool _bindSaving = false;
  List<AICharacter> _dyChars = [];
  bool _dyCharsLoading = false;

  /// 48a：插件图标展示（manifest.icon 相对路径 → 页面托管 URL；加载失败回退 type 图标）
  Widget _iconWidget(String name, String type, String category, String icon) {
    final fallback = Container(
      width: 36,
      height: 36,
      decoration: BoxDecoration(
        color: pluginTypeColor(context, type, category).withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Icon(
        pluginTypeIcon(type, category),
        size: 19,
        color: pluginTypeColor(context, type, category),
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
        // #65：插件图标走后端 plugin_page（HTTPBearer），裸加载会 401，必须带 Authorization 头。
        // 缓存说明：Flutter 图片缓存 key 仅含 URL（不含 headers），而单机同一时刻只有一条登录态，
        // 故带头加载成功后按 URL 缓存的是当前会话的授权图，不会串用户；若切换账号需 evict 该 URL。
        headers: pluginAuthHeaders(ApiClient().token),
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
      _dyBoundCharId = _parseBoundChar(cfg['allowed_character_ids']);
      _loadDyChars();
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
                      color: pluginTypeColor(context, type, category).withValues(alpha: 0.18),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(pluginTypeLabel(context, type, category),
                        style: TextStyle(fontSize: 11, color: pluginTypeColor(context, type, category))),
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
                  // 3.9：来源 + 校验和短值（非内置才显示来源徽标；sha256 有则显示）
                  if ((p['source'] as String? ?? 'builtin') != 'builtin')
                    Text(_sourceLabel(l10n, p['source'] as String? ?? 'builtin'),
                        style: const TextStyle(fontSize: 11, color: Colors.purple)),
                  if ((p['sha256'] as String? ?? '').isNotEmpty)
                    Text('${l10n.pluginSha256}: ${_shaShort(p['sha256'] as String)}',
                        style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
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
                child: PluginConfigForm(
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
                  child: ZeroCodeConfigEditor(
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
        // #68 P5：绑定角色（组级唯一单选；-1=未绑定）
        if (widget.isAdmin) ...[
          Text(l10n.douyinBindRole, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
          const SizedBox(height: 2),
          Text(l10n.douyinBindRoleHint, style: TextStyle(fontSize: 10, color: Colors.grey)),
          const SizedBox(height: 6),
          if (_dyCharsLoading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 6),
              child: Text('加载中…', style: TextStyle(fontSize: 12, color: Colors.grey)),
            )
          else
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(10),
              ),
              child: DropdownButton<int>(
                value: _dyBoundCharId,
                isExpanded: true,
                underline: const SizedBox.shrink(),
                isDense: true,
                items: [
                  DropdownMenuItem<int>(
                      value: -1, child: Text(l10n.douyinBindNone, style: const TextStyle(fontSize: 13))),
                  for (final c in _dyChars)
                    DropdownMenuItem<int>(value: c.id, child: Text(c.name, style: const TextStyle(fontSize: 13))),
                ],
                onChanged: _bindSaving ? null : (v) => setState(() => _dyBoundCharId = v ?? -1),
              ),
            ),
          const SizedBox(height: 6),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              onPressed: _bindSaving ? null : _saveDyBindRole,
              style: FilledButton.styleFrom(
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 14),
              ),
              child: Text(l10n.douyinBindSave, style: const TextStyle(fontSize: 12)),
            ),
          ),
          const SizedBox(height: 10),
        ],
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

  /// #68 P5：解析当前 douyin 绑定角色 id（兼容历史逗号分隔字符串/数组；-1=未绑定）
  int _parseBoundChar(dynamic raw) {
    if (raw == null) return -1;
    if (raw is List) {
      for (final x in raw) {
        final i = int.tryParse('$x');
        if (i != null) return i;
      }
      return -1;
    }
    final s = '$raw'.trim();
    if (s.isEmpty) return -1;
    for (final p in s.split(',').map((e) => e.trim()).where((e) => e.isNotEmpty)) {
      final i = int.tryParse(p);
      if (i != null) return i;
    }
    return -1;
  }

  Future<void> _loadDyChars() async {
    if (!mounted) return;
    setState(() => _dyCharsLoading = true);
    try {
      final chars = await ApiClient().getCharacters();
      if (!mounted) return;
      setState(() {
        _dyChars = chars.where((c) => c.isActive).toList();
        _dyCharsLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _dyCharsLoading = false);
    }
  }

  Future<void> _saveDyBindRole() async {
    final l10n = AppLocalizations.of(context)!;
    if (_bindSaving) return;
    setState(() => _bindSaving = true);
    try {
      final cfg = Map<String, dynamic>.from(
          widget.plugin['config'] as Map<String, dynamic>? ?? {});
      cfg['allowed_character_ids'] = _dyBoundCharId >= 0 ? <int>[_dyBoundCharId] : <int>[];
      await ApiClient().updatePlugin('douyin_mcp', config: cfg);
      widget.onToast(l10n.douyinBindSaved);
      widget.onChanged();
    } catch (e) {
      widget.onToast(l10n.extSaveFailed('$e'));
    } finally {
      if (mounted) setState(() => _bindSaving = false);
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
