import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';

/// 花园受限 user key 绑定与只读浏览页。
/// key 只在本页输入并通过 API 提交，不写日志/不回显；默认只读，观察需显式勾选。
class GardenForumSetupScreen extends StatefulWidget {
  const GardenForumSetupScreen({super.key});

  @override
  State<GardenForumSetupScreen> createState() => _GardenForumSetupScreenState();
}

class _GardenForumSetupScreenState extends State<GardenForumSetupScreen> {
  final _baseCtrl = TextEditingController();
  final _keyCtrl = TextEditingController();
  final _api = ApiClient();
  List<AICharacter> _chars = [];
  AICharacter? _selected;
  Map<String, dynamic>? _account;
  Map<String, dynamic>? _observation;
  bool _busy = false;
  bool _observe = false;

  @override
  void initState() {
    super.initState();
    _loadChars();
  }

  @override
  void dispose() {
    _baseCtrl.dispose();
    _keyCtrl.dispose();
    super.dispose();
  }

  Future<Map<String, dynamic>> _bindApi(int id, String key, String base) async {
    final r = await _api.dio.post('/api/v1/plugins/garden_forum/account',
        data: {'character_id': id, 'user_key': key, 'base_url': base});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> _statusApi(int id) async {
    final r = await _api.dio.get('/api/v1/plugins/garden_forum/account',
        queryParameters: {'character_id': id});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> _unbindApi(int id) async {
    final r = await _api.dio.delete('/api/v1/plugins/garden_forum/account',
        data: {'character_id': id});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> _browseApi(int id, bool observe) async {
    final r = await _api.dio.post('/api/v1/plugins/garden_forum/user/readonly',
        data: {'character_id': id, 'observe': observe});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> _obsApi(int id) async {
    final r = await _api.dio.get('/api/v1/plugins/garden_forum/user/observation',
        queryParameters: {'character_id': id});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> _clearObsApi(int id) async {
    final r = await _api.dio.delete('/api/v1/plugins/garden_forum/user/observation',
        data: {'character_id': id});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<void> _loadChars() async {
    try {
      final list = await _api.getCharacters();
      if (!mounted) return;
      setState(() {
        _chars = list.where((c) => c.isActive).toList();
        _selected = _chars.isEmpty ? null : _chars.first;
      });
      await _refresh();
    } catch (e) {
      _toast(_l10n.gardenLoadFailed);
    }
  }

  Future<void> _refresh() async {
    final c = _selected;
    if (c == null) return;
    try {
      final a = await _statusApi(c.id);
      final o = a['bound'] == true ? await _obsApi(c.id) : null;
      if (!mounted) return;
      setState(() {
        _account = a;
        _observation = o;
        if ((a['base_url'] as String?)?.isNotEmpty == true && _baseCtrl.text.isEmpty) {
          _baseCtrl.text = a['base_url'] as String;
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _account = {'bound': false});
    }
  }

  AppLocalizations get _l10n => AppLocalizations.of(context)!;

  void _toast(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _run(Future<void> Function() fn) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await fn();
    } catch (e) {
      _toast(_l10n.gardenLoadFailed);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _bind() async {
    final c = _selected;
    final key = _keyCtrl.text.trim();
    final base = _baseCtrl.text.trim();
    if (c == null || key.isEmpty || base.isEmpty) {
      _toast(_l10n.gardenKeyHint);
      return;
    }
    await _run(() async {
      await _bindApi(c.id, key, base);
      _keyCtrl.clear();
      await _refresh();
      _toast(_l10n.gardenBindSuccess);
    });
  }

  Future<void> _unbind() async {
    final c = _selected;
    if (c == null) return;
    await _run(() async {
      await _unbindApi(c.id);
      await _refresh();
      _toast(_l10n.gardenUnbindSuccess);
    });
  }

  Future<void> _browse() async {
    final c = _selected;
    if (c == null) return;
    await _run(() async {
      await _browseApi(c.id, _observe);
      await _refresh();
      _toast(_l10n.gardenBrowseDone);
    });
  }

  Future<void> _clear() async {
    final c = _selected;
    if (c == null) return;
    await _run(() async {
      await _clearObsApi(c.id);
      await _refresh();
      _toast(_l10n.gardenObservationCleared);
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = _l10n;
    final bound = _account?['bound'] == true;
    final items = (_observation?['items'] as List?) ?? const [];
    return Scaffold(
      appBar: AppBar(title: Text(l10n.gardenSetupTitle)),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Text(l10n.gardenPrivacyHint, style: const TextStyle(fontSize: 12)),
        const SizedBox(height: 12),
        DropdownButtonFormField<AICharacter>(
          initialValue: _selected,
          decoration: InputDecoration(labelText: l10n.gardenCharacter),
          items: _chars.map((c) => DropdownMenuItem(value: c, child: Text(c.name))).toList(),
          onChanged: (v) { setState(() => _selected = v); _refresh(); },
        ),
        TextField(
          controller: _baseCtrl,
          decoration: InputDecoration(labelText: l10n.gardenBaseUrl),
        ),
        TextField(
          controller: _keyCtrl,
          obscureText: true,
          enableSuggestions: false,
          autocorrect: false,
          decoration: InputDecoration(labelText: l10n.gardenUserKey, helperText: l10n.gardenKeyHint),
        ),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: [
          FilledButton(onPressed: _busy ? null : _bind, child: Text(l10n.gardenBind)),
          OutlinedButton(onPressed: _busy || !bound ? null : _unbind, child: Text(l10n.gardenUnbind)),
          OutlinedButton(onPressed: _busy || !bound ? null : _browse, child: Text(l10n.gardenBrowse)),
          OutlinedButton(onPressed: _busy || !bound ? null : _clear, child: Text(l10n.gardenClearObservation)),
        ]),
        CheckboxListTile(
          value: _observe,
          onChanged: (v) => setState(() => _observe = v ?? false),
          title: Text(l10n.gardenObserve),
        ),
        const Divider(),
        Text(bound ? l10n.gardenBound : l10n.gardenNotBound),
        if (bound) Text('role: ${_account?['role_id'] ?? ''}'),
        Text(l10n.gardenModelHint, style: const TextStyle(fontSize: 12)),
        if (items.isNotEmpty) ...[
          const SizedBox(height: 8),
          for (final it in items.take(5))
            ListTile(dense: true, title: Text((it as Map)['title']?.toString() ?? '')),
        ],
      ]),
    );
  }
}
