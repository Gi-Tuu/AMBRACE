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
  // 一机多主（S3，2026-09-05）：渠道绑定统一区块（wechat/douyin 共用；走 /channels/{ch}/bindings，
  // 不再 updatePlugin config / rebindWechatPlugin）。「添加另一个 bot」本期隐藏（多 bot 待真机稳定键验证）。
  final Map<String, List<Map<String, dynamic>>> _chBindings = {};
  final Map<String, int> _chSelected = {};
  List<AICharacter> _chChars = [];
  bool _chCharsLoading = false;
  final Set<String> _chSaving = {};

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
      _loadChannelBindings('douyin');
    }
    if (widget.plugin['name'] == 'wechat_ilink') {
      _loadChannelBindings('wechat');
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
            if (name == 'douyin_mcp' && _enabled) ...[
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8, right: 8),
                child: _buildChannelBindingTile('douyin'),
              ),
              Padding(
                padding: const EdgeInsets.only(left: 42, right: 8),
                child: _buildDyCreator(),
              ),
            ],
            // 一机多主（S3）：微信绑定角色区块（主账号可写、子账号只读；走统一渠道绑定 API）
            if (name == 'wechat_ilink' && _enabled)
              Padding(
                padding: const EdgeInsets.only(left: 42, top: 8, right: 8),
                child: _buildChannelBindingTile('wechat'),
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

  // ================================================================= 一机多主（S3）：渠道绑定统一区块

  Future<void> _loadChannelBindings(String channel) async {
    if (!mounted) return;
    setState(() => _chCharsLoading = true);
    try {
      final items = await ApiClient().listChannelBindings(channel);
      // C4（2026-09-05 审查）：主/子账号都加载家庭角色——子账号用于把绑定 cid 显示成角色名
      final chars = await ApiClient().getCharacters();
      if (!mounted) return;
      setState(() {
        _chBindings[channel] = items;
        if (items.isNotEmpty) {
          final cid = items.first['character_id'];
          _chSelected[channel] = cid is int ? cid : int.tryParse('$cid') ?? -1;
        } else {
          _chSelected[channel] = -1;
        }
        _chChars = chars.where((c) => c.isActive).toList();
        _chCharsLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _chCharsLoading = false);
    }
  }

  /// 保存绑定（仅主账号）：选中角色 → PUT；选「未绑定」(-1) → DELETE（解绑）。
  Future<void> _saveChannelBinding(String channel, String botAccountId) async {
    final l10n = AppLocalizations.of(context)!;
    if (_chSaving.contains(channel)) return;
    final cid = _chSelected[channel] ?? -1;
    if (cid < 0 && botAccountId != 'default') {
      widget.onToast(l10n.channelBindingNeedPick);
      return;
    }
    setState(() => _chSaving.add(channel));
    try {
      if (cid < 0) {
        await ApiClient().deleteChannelBinding(channel, botAccountId);
        widget.onToast(l10n.channelBindingUnbound);
      } else {
        await ApiClient().putChannelBinding(channel, botAccountId, cid);
        widget.onToast(l10n.channelBindingSaved);
      }
      await _loadChannelBindings(channel);
      widget.onChanged();
    } catch (e) {
      widget.onToast(l10n.extSaveFailed('$e'));
    } finally {
      if (mounted) setState(() => _chSaving.remove(channel));
    }
  }

  /// 解绑（仅主账号，二次确认）。
  Future<void> _unbindChannelBinding(String channel, String botAccountId) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(l10n.channelBindingUnbind),
        content: Text(l10n.channelBindingUnbindConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: Text(l10n.channelBindingUnbind)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _chSaving.add(channel));
    try {
      await ApiClient().deleteChannelBinding(channel, botAccountId);
      widget.onToast(l10n.channelBindingUnbound);
      await _loadChannelBindings(channel);
      widget.onChanged();
    } catch (e) {
      widget.onToast(l10n.extSaveFailed('$e'));
    } finally {
      if (mounted) setState(() => _chSaving.remove(channel));
    }
  }

  /// C4：子账号只读视图把绑定 character_id 解析成角色名（找不到/未绑定 → 「未绑定」文案）。
  String _boundCharName(AppLocalizations l10n, dynamic cid) {
    final id = cid is int ? cid : int.tryParse('$cid');
    if (id == null || id < 0) return l10n.channelBindingNone;
    for (final c in _chChars) {
      if (c.id == id) return c.name;
    }
    return l10n.channelBindingNone;
  }

  /// 渠道绑定统一区块（S3）：按当前主账号列 bot 绑定；主账号可换绑/解绑，子账号只读。
  /// 「添加另一个 bot」本期隐藏（多 bot 需真机稳定键验证后再开放）。
  Widget _buildChannelBindingTile(String channel) {
    final l10n = AppLocalizations.of(context)!;
    final rows = _chBindings[channel] ?? const <Map<String, dynamic>>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(l10n.channelBindingRole, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        if (widget.isAdmin)
          Text(l10n.channelBindingRoleHint, style: TextStyle(fontSize: 10, color: Colors.grey))
        else
          Text(l10n.channelBindingMainOnly, style: TextStyle(fontSize: 10, color: Colors.grey)),
        const SizedBox(height: 6),
        if (_chCharsLoading)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Text(l10n.channelBindingLoading, style: const TextStyle(fontSize: 12, color: Colors.grey)),
          )
        else if (rows.isEmpty)
          Text(l10n.channelBindingEmpty, style: const TextStyle(fontSize: 12, color: Colors.grey))
        else
          for (final row in rows) ...[
            Text(
              (row['bot_label'] as String? ?? '').isNotEmpty
                  ? row['bot_label'] as String
                  : l10n.channelBindingBotDefault,
              style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
            ),
            const SizedBox(height: 2),
            // C4（2026-09-05 审查）：子账号纯文本只读显示绑定角色名，不渲染 Dropdown
            // （子账号 selected cid 可能不在其可写角色集，Dropdown value 断言会崩）；
            // 主账号保持 Dropdown 可写。
            if (!widget.isAdmin)
              Text(
                _boundCharName(l10n, row['character_id']),
                style: const TextStyle(fontSize: 13),
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
                  value: _chSelected[channel] ?? -1,
                  isExpanded: true,
                  underline: const SizedBox.shrink(),
                  isDense: true,
                  items: [
                    DropdownMenuItem<int>(
                        value: -1, child: Text(l10n.channelBindingNone, style: const TextStyle(fontSize: 13))),
                    for (final c in _chChars)
                      DropdownMenuItem<int>(value: c.id, child: Text(c.name, style: const TextStyle(fontSize: 13))),
                  ],
                  onChanged: _chSaving.contains(channel)
                      ? null
                      : (v) => setState(() => _chSelected[channel] = v ?? -1),
                ),
              ),
            if (widget.isAdmin) ...[
              const SizedBox(height: 6),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: _chSaving.contains(channel)
                        ? null
                        : () => _unbindChannelBinding(channel, row['bot_account_id'] as String? ?? 'default'),
                    child: Text(l10n.channelBindingUnbind, style: const TextStyle(fontSize: 12)),
                  ),
                  const SizedBox(width: 4),
                  FilledButton.tonal(
                    // C8：选中「未绑定」时禁用保存（删除统一走「解绑」按钮，防误触直接 DELETE）
                    onPressed: (_chSaving.contains(channel) || (_chSelected[channel] ?? -1) < 0)
                        ? null
                        : () => _saveChannelBinding(channel, row['bot_account_id'] as String? ?? 'default'),
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                    ),
                    child: Text(l10n.channelBindingSave, style: const TextStyle(fontSize: 12)),
                  ),
                ],
              ),
            ],
            const SizedBox(height: 6),
          ],
        const SizedBox(height: 4),
      ],
    );
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
