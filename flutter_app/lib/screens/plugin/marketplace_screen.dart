import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';

/// 插件市场页：内置 + 远程市场（来源徽标 / 一键安装 / 更新 / 远程配置）
class MarketplaceScreen extends StatefulWidget {
  const MarketplaceScreen({super.key});

  @override
  State<MarketplaceScreen> createState() => _MarketplaceScreenState();
}

enum _MarketFilter { all, plugin, mcp }

class _MarketplaceScreenState extends State<MarketplaceScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  String? _error;
  _MarketFilter _filter = _MarketFilter.all;
  String _query = '';
  List<Map<String, dynamic>> _items = [];
  final _searchCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().userId == 1;
    _load();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await ApiClient().getMarketplace();
      if (!mounted) return;
      setState(() {
        _items = items;
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

  Future<void> _install(Map<String, dynamic> item) async {
    final l10n = AppLocalizations.of(context)!;
    final isRemote = (item['source'] as String? ?? 'builtin') != 'builtin';
    // 远程条目安装前二次确认（第三方代码与服务器同权限）
    if (isRemote) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(l10n.marketRemoteConfig),
          content: Text(l10n.marketRemoteInstallTip),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: Text(l10n.cancel),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: Text(l10n.marketInstall),
            ),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }
    try {
      await ApiClient().installMarketplacePlugin(item['name'] as String);
      if (!mounted) return;
      _toast(l10n.marketInstallSuccess);
      await _load();
    } catch (e) {
      _toast('${l10n.marketInstallFailed}: ${_errMsg(e)}');
    }
  }

  String _errMsg(Object e) =>
      e.toString().replaceFirst('DioException [bad response]: ', '');

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openRemoteConfig() async {
    await Navigator.push(context, MaterialPageRoute(
      builder: (_) => const _RemoteMarketConfigScreen(),
    ));
    if (mounted) await _load();
  }

  String _firstChar(String name) {
    return name.isEmpty ? '?' : name[0].toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final filtered = _items.where((it) {
      final cat = it['category'] as String? ?? 'plugin';
      if (_filter == _MarketFilter.plugin && cat != 'plugin') return false;
      if (_filter == _MarketFilter.mcp && cat != 'mcp') return false;
      final q = _query.trim().toLowerCase();
      if (q.isNotEmpty) {
        final name = (it['name'] as String? ?? '').toLowerCase();
        final desc = (it['description'] as String? ?? '').toLowerCase();
        if (!name.contains(q) && !desc.contains(q)) return false;
      }
      return true;
    }).toList();

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.marketplace),
        actions: [
          if (_isAdmin)
            IconButton(
              tooltip: l10n.marketRemoteConfig,
              icon: const Icon(Icons.cloud_sync_outlined),
              onPressed: _openRemoteConfig,
            ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: TextField(
              controller: _searchCtrl,
              onChanged: (v) => setState(() => _query = v),
              decoration: InputDecoration(
                hintText: l10n.marketSearchHint,
                prefixIcon: const Icon(Icons.search, size: 20),
                suffixIcon: _query.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () {
                          _searchCtrl.clear();
                          setState(() => _query = '');
                        },
                      ),
                isDense: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.55),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
            child: Row(
              children: [
                _chip(l10n.pluginAll, _MarketFilter.all),
                const SizedBox(width: 8),
                _chip(l10n.pluginNormal, _MarketFilter.plugin),
                const SizedBox(width: 8),
                _chip('MCP', _MarketFilter.mcp),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(child: Text(_error!))
                    : filtered.isEmpty
                        ? Center(child: Text(l10n.marketNoResult))
                        : ListView.builder(
                            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                            itemCount: filtered.length,
                            itemBuilder: (c, i) => _card(c, filtered[i], l10n),
                          ),
          ),
        ],
      ),
    );
  }

  Widget _chip(String label, _MarketFilter f) {
    final selected = _filter == f;
    return ChoiceChip(
      label: Text(label, style: const TextStyle(fontSize: 13)),
      selected: selected,
      onSelected: (_) => setState(() => _filter = f),
      visualDensity: VisualDensity.compact,
      showCheckmark: false,
    );
  }

  Widget _sourceBadge(Map<String, dynamic> item, AppLocalizations l10n) {
    final source = item['source'] as String? ?? 'builtin';
    if (source == 'builtin') {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: Colors.blue.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(l10n.marketSourceBuiltin,
            style: const TextStyle(fontSize: 10, color: Colors.blue)),
      );
    }
    final market = source.startsWith('remote:')
        ? source.substring(7)
        : l10n.marketSourceRemote;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.purple.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text('${l10n.marketSourceRemote} · $market',
          style: const TextStyle(fontSize: 10, color: Colors.purple)),
    );
  }

  Widget _card(BuildContext context, Map<String, dynamic> item, AppLocalizations l10n) {
    final installed = item['installed'] == true;
    final enabled = item['enabled'] == true;
    final category = item['category'] as String? ?? 'plugin';
    final author = item['author'] as String? ?? '';
    final version = item['version'] as String? ?? '';
    final installedVersion = item['installed_version'] as String? ?? '';
    final source = item['source'] as String? ?? 'builtin';
    final isRemote = source != 'builtin';
    final hasUpdate = installed && isRemote && version.isNotEmpty && version != installedVersion;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () async {
          await Navigator.push(context, MaterialPageRoute(
            builder: (_) => MarketplaceDetailScreen(item: item),
          ));
          if (mounted) await _load();
        },
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 34,
                    height: 34,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: category == 'mcp'
                          ? Theme.of(context).colorScheme.tertiaryContainer
                          : Theme.of(context).colorScheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(9),
                    ),
                    child: Text(
                      _firstChar(item['name'] as String? ?? ''),
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Flexible(
                              child: Text(
                                item['name'] as String? ?? '',
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            const SizedBox(width: 6),
                            Text('v$version',
                                style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
                          ],
                        ),
                        if (author.isNotEmpty)
                          Text('${l10n.pluginAuthor}：$author',
                              style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  _sourceBadge(item, l10n),
                  const SizedBox(width: 6),
                  if (installed)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: Colors.green.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        enabled ? '${l10n.marketInstalled} · ${l10n.connected}' : l10n.marketInstalled,
                        style: const TextStyle(fontSize: 11, color: Colors.green),
                      ),
                    )
                  else if (_isAdmin)
                    FilledButton.tonal(
                      onPressed: () => _install(item),
                      style: FilledButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                      ),
                      child: Text(l10n.marketInstall),
                    ),
                ],
              ),
              if (hasUpdate)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Align(
                    alignment: Alignment.centerRight,
                    child: FilledButton.tonalIcon(
                      onPressed: () => _install(item),
                      style: FilledButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                      ),
                      icon: const Icon(Icons.system_update_alt, size: 16),
                      label: Text(l10n.marketRemoteUpdate),
                    ),
                  ),
                ),
              const SizedBox(height: 8),
              Text(
                item['description'] as String? ?? '',
                style: const TextStyle(fontSize: 12.5, color: Color(0xFF6E6E73)),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 远程市场配置页：启用 / URLs 增删 / 刷新间隔 / 白名单 / 大小上限 / 立即刷新
class _RemoteMarketConfigScreen extends StatefulWidget {
  const _RemoteMarketConfigScreen();

  @override
  State<_RemoteMarketConfigScreen> createState() => _RemoteMarketConfigScreenState();
}

class _RemoteMarketConfigScreenState extends State<_RemoteMarketConfigScreen> {
  bool _loading = true;
  bool _saving = false;
  bool _refreshing = false;
  String? _error;
  bool _enabled = false;
  int _interval = 24;
  int _maxZipMb = 10;
  final List<String> _urls = [];
  final List<String> _hosts = [];
  final _urlCtrl = TextEditingController();
  final _hostCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _urlCtrl.dispose();
    _hostCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final cfg = await ApiClient().getMarketplaceConfig();
      if (!mounted) return;
      setState(() {
        _enabled = cfg['enabled'] == true;
        _interval = (cfg['refresh_interval_hours'] as num?)?.toInt() ?? 24;
        _maxZipMb = (cfg['max_zip_mb'] as num?)?.toInt() ?? 10;
        _urls
          ..clear()
          ..addAll((cfg['urls'] as List? ?? []).cast<String>());
        _hosts
          ..clear()
          ..addAll((cfg['allowed_hosts'] as List? ?? []).cast<String>());
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _saving = true);
    try {
      await ApiClient().updateMarketplaceConfig({
        'enabled': _enabled,
        'urls': _urls,
        'refresh_interval_hours': _interval,
        'allowed_hosts': _hosts,
        'max_zip_mb': _maxZipMb,
      });
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.marketRemoteSaved)));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('${l10n.marketInstallFailed}: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _refreshNow({bool force = false}) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _refreshing = true);
    try {
      final r = await ApiClient().refreshMarketplace(force: force);
      if (!mounted) return;
      final ok = (r['total_ok'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.marketRemoteRefreshed(ok))));
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('${l10n.marketInstallFailed}: $e')));
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.marketRemoteConfig)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                  children: [
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(l10n.marketRemoteEnabled),
                      value: _enabled,
                      onChanged: (v) => setState(() => _enabled = v),
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        Expanded(
                          child: FilledButton.tonalIcon(
                            onPressed: _refreshing ? null : () => _refreshNow(),
                            icon: _refreshing
                                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                                : const Icon(Icons.refresh, size: 18),
                            label: Text(l10n.marketRemoteRefreshNow),
                          ),
                        ),
                        const SizedBox(width: 10),
                        if (_urls.isNotEmpty)
                          TextButton(
                            onPressed: _refreshing ? null : () => _refreshNow(force: true),
                            child: Text(l10n.marketRemoteRefreshNow),
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Text(l10n.marketRemoteUrls,
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    for (final u in _urls)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.link, size: 18),
                        title: Text(u, style: const TextStyle(fontSize: 12.5), overflow: TextOverflow.ellipsis),
                        trailing: IconButton(
                          icon: const Icon(Icons.close, size: 18),
                          onPressed: () => setState(() => _urls.remove(u)),
                        ),
                      ),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _urlCtrl,
                            decoration: InputDecoration(
                              hintText: l10n.marketRemoteUrlsHint,
                              isDense: true,
                              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        IconButton.filledTonal(
                          icon: const Icon(Icons.add, size: 20),
                          onPressed: () {
                            final v = _urlCtrl.text.trim();
                            if (v.isNotEmpty && !_urls.contains(v)) {
                              setState(() => _urls.add(v));
                              _urlCtrl.clear();
                            }
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Text(l10n.marketRemoteRefreshInterval,
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    TextField(
                      keyboardType: TextInputType.number,
                      controller: TextEditingController(text: '$_interval'),
                      onChanged: (v) => _interval = int.tryParse(v) ?? 24,
                      decoration: const InputDecoration(isDense: true, border: OutlineInputBorder()),
                    ),
                    const SizedBox(height: 16),
                    Text(l10n.marketRemoteMaxZip,
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    TextField(
                      keyboardType: TextInputType.number,
                      controller: TextEditingController(text: '$_maxZipMb'),
                      onChanged: (v) => _maxZipMb = int.tryParse(v) ?? 10,
                      decoration: const InputDecoration(isDense: true, border: OutlineInputBorder()),
                    ),
                    const SizedBox(height: 16),
                    Text(l10n.marketRemoteAllowedHosts,
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    for (final h in _hosts)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.public, size: 18),
                        title: Text(h, style: const TextStyle(fontSize: 12.5)),
                        trailing: IconButton(
                          icon: const Icon(Icons.close, size: 18),
                          onPressed: () => setState(() => _hosts.remove(h)),
                        ),
                      ),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _hostCtrl,
                            decoration: InputDecoration(
                              hintText: l10n.marketRemoteAllowedHostsHint,
                              isDense: true,
                              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        IconButton.filledTonal(
                          icon: const Icon(Icons.add, size: 20),
                          onPressed: () {
                            final v = _hostCtrl.text.trim();
                            if (v.isNotEmpty && !_hosts.contains(v)) {
                              setState(() => _hosts.add(v));
                              _hostCtrl.clear();
                            }
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _saving ? null : _save,
                      style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(46)),
                      child: Text(l10n.marketRemoteSave),
                    ),
                  ],
                ),
    );
  }
}

/// 市场条目详情页：usage/readme/hooks/permissions/风险提示 + 安装
class MarketplaceDetailScreen extends StatefulWidget {
  const MarketplaceDetailScreen({super.key, required this.item});

  final Map<String, dynamic> item;

  @override
  State<MarketplaceDetailScreen> createState() => _MarketplaceDetailScreenState();
}

class _MarketplaceDetailScreenState extends State<MarketplaceDetailScreen> {
  bool _installing = false;

  Future<void> _installDetail() async {
    final l10n = AppLocalizations.of(context)!;
    final isRemote = (widget.item['source'] as String? ?? 'builtin') != 'builtin';
    if (isRemote) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(l10n.marketRemoteConfig),
          content: Text(l10n.marketRemoteInstallTip),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: Text(l10n.cancel),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: Text(l10n.marketInstall),
            ),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }
    setState(() => _installing = true);
    try {
      await ApiClient().installMarketplacePlugin(widget.item['name'] as String);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.marketInstallSuccess)));
      Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      final s = e.toString().replaceFirst('DioException [bad response]: ', '');
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('${l10n.marketInstallFailed}: $s')));
    } finally {
      if (mounted) setState(() => _installing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final item = widget.item;
    final installed = item['installed'] == true;
    final isAdmin = context.read<SettingsProvider>().userId == 1;
    final category = item['category'] as String? ?? 'plugin';
    final hooks = (item['hooks'] as List? ?? []).cast<String>();
    final perms = (item['permissions'] as List? ?? []).cast<String>();
    final usage = item['usage'] as String? ?? '';
    final readme = item['readme_text'] as String? ?? '';
    final source = item['source'] as String? ?? 'builtin';
    final installedVersion = item['installed_version'] as String? ?? '';
    final version = item['version'] as String? ?? '';
    final hasUpdate = installed && source != 'builtin' && version.isNotEmpty && version != installedVersion;

    return Scaffold(
      appBar: AppBar(title: Text(item['name'] as String? ?? '')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: category == 'mcp'
                      ? Theme.of(context).colorScheme.tertiaryContainer
                      : Theme.of(context).colorScheme.secondaryContainer,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(category == 'mcp' ? l10n.pluginMcp : l10n.pluginNormal,
                    style: const TextStyle(fontSize: 12)),
              ),
              const SizedBox(width: 8),
              Text('v${item['version'] ?? ''}',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93))),
              const SizedBox(width: 8),
              Text('${l10n.pluginAuthor}：${item['author'] ?? ''}',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93))),
            ],
          ),
          const SizedBox(height: 10),
          Text(item['description'] as String? ?? '',
              style: const TextStyle(fontSize: 14, height: 1.5)),
          if (usage.isNotEmpty) ...[
            const SizedBox(height: 16),
            _sectionTitle('使用教程'),
            const SizedBox(height: 6),
            _sectionBody(usage),
          ],
          if (readme.isNotEmpty) ...[
            const SizedBox(height: 16),
            _sectionTitle('README'),
            const SizedBox(height: 6),
            _sectionBody(readme),
          ],
          if (hooks.isNotEmpty) ...[
            const SizedBox(height: 16),
            _sectionTitle(l10n.marketDetailHooks),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: hooks.map((h) => _tag(h)).toList(),
            ),
          ],
          if (perms.isNotEmpty) ...[
            const SizedBox(height: 16),
            _sectionTitle(l10n.marketDetailPermissions),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: perms.map((p) => _tag(p)).toList(),
            ),
          ],
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.orange.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.orange.withValues(alpha: 0.3)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.warning_amber_rounded, size: 18, color: Colors.orange),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(l10n.marketRiskTip,
                      style: const TextStyle(fontSize: 12.5, color: Color(0xFF8A5A00))),
                ),
              ],
            ),
          ),
          if (!installed && isAdmin) ...[
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _installing ? null : _installDetail,
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(46),
              ),
              child: _installing
                  ? const SizedBox(
                      width: 20, height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(l10n.marketInstall),
            ),
          ],
          if (hasUpdate) ...[
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _installing ? null : _installDetail,
              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(46)),
              child: _installing
                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(l10n.marketRemoteUpdate),
            ),
          ],
        ],
      ),
    );
  }

  Widget _sectionTitle(String t) =>
      Text(t, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600));

  Widget _sectionBody(String t) =>
      Text(t, style: const TextStyle(fontSize: 12.5, height: 1.5, color: Color(0xFF6E6E73)));

  Widget _tag(String t) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(t, style: const TextStyle(fontSize: 11.5)),
      );
}
