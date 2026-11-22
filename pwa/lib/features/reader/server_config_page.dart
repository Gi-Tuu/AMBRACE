import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 服务器级配置管理页（2026-11-22 批 12）：LLM 全局 / 语音 / 识图 / 生图（含日限）
/// / 任务目录——对齐 Flutter App `api_config_screen.dart` 的管理面。
/// 仅主账号可操作（后端 `require_server_admin` 强校验）。
class ServerConfigPage extends StatefulWidget {
  const ServerConfigPage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<ServerConfigPage> createState() => _ServerConfigPageState();
}

class _CfgSection {
  final String key;
  final String title;
  bool enabled = false;
  bool hasApiKey = false;
  String baseUrl = '';
  String model = '';
  String provider = '';
  String dailyLimit = ''; // 仅生图

  _CfgSection({required this.key, required this.title});

  Map<String, dynamic> toBody() => {
        'enabled': enabled,
        'base_url': baseUrl,
        'model': model,
        'provider': provider,
        if (key == 'image_gen') 'daily_limit': int.tryParse(dailyLimit) ?? 10,
      };
}

class _ServerConfigPageState extends State<ServerConfigPage> {
  late final ApiConfigApi _api = ApiConfigApi(widget.client);
  bool _loading = true;
  String _error = '';
  bool _saving = false;
  String _savedKey = '';

  late _CfgSection _llm;
  late _CfgSection _speech;
  late _CfgSection _vlm;
  late _CfgSection _imageGen;
  List<Map<String, dynamic>> _tasks = const [];
  Map<String, Map<String, dynamic>> _taskCfgs = {};
  String _selectedTask = '';

  @override
  void initState() {
    super.initState();
    _llm = _CfgSection(key: 'llm', title: 'LLM');
    _speech = _CfgSection(key: 'speech', title: '语音');
    _vlm = _CfgSection(key: 'vlm', title: '识图');
    _imageGen = _CfgSection(key: 'image_gen', title: '生图');
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final srv = await _api.serverApiConfig();
      _llm.enabled = srv['enabled'] == true;
      _llm.hasApiKey = srv['has_api_key'] == true;
      _llm.baseUrl = srv['base_url'] as String? ?? '';
      _llm.model = srv['model'] as String? ?? '';
      _llm.provider = srv['provider'] as String? ?? '';

      final sp = await _api.serverSpeechConfig();
      _speech.enabled = sp['enabled'] == true;
      _speech.hasApiKey = sp['has_api_key'] == true;
      _speech.baseUrl = sp['base_url'] as String? ?? '';
      _speech.model = sp['model'] as String? ?? '';
      _speech.provider = sp['provider'] as String? ?? '';

      final vlm = await _api.serverVlmConfig();
      _vlm.enabled = vlm['enabled'] == true;
      _vlm.hasApiKey = vlm['has_api_key'] == true;
      _vlm.baseUrl = vlm['base_url'] as String? ?? '';
      _vlm.model = vlm['model'] as String? ?? '';

      final img = await _api.serverImageGenConfig();
      _imageGen.enabled = img['enabled'] == true;
      _imageGen.hasApiKey = img['has_api_key'] == true;
      _imageGen.baseUrl = img['base_url'] as String? ?? '';
      _imageGen.model = img['model'] as String? ?? '';
      _imageGen.provider = img['provider'] as String? ?? '';
      _imageGen.dailyLimit = '${img['daily_limit'] ?? 10}';

      _tasks = await _api.taskCatalog();
      _taskCfgs = {};
      for (final t in _tasks) {
        final task = t['task'] as String? ?? '';
        if (task.isEmpty) continue;
        try {
          _taskCfgs[task] = await _api.serverTaskConfig(task);
        } catch (_) {}
      }
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (_tasks.isNotEmpty) _selectedTask = _tasks.first['task'] as String;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _save(String key, Map<String, dynamic> body) async {
    setState(() {
      _saving = true;
      _savedKey = '';
    });
    try {
      switch (key) {
        case 'llm':
          await _api.saveServerApiConfig(body);
        case 'speech':
          await _api.saveServerSpeechConfig(body);
        case 'vlm':
          await _api.saveServerVlmConfig(body);
        case 'image_gen':
          await _api.saveServerImageGenConfig(body);
        case 'task':
          await _api.saveServerTaskConfig(_selectedTask, body);
      }
      if (!mounted) return;
      setState(() => _savedKey = key);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(widget.rs.t('cfgSaved'))));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('${widget.rs.t('cfgSaveFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    if (_loading) {
      return Scaffold(
          appBar: AppBar(title: Text(rs.t('srvCfgTitle'))),
          body: const Center(child: CircularProgressIndicator()));
    }
    if (_error.isNotEmpty) {
      return Scaffold(
          appBar: AppBar(title: Text(rs.t('srvCfgTitle'))),
          body: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error, style: theme.textTheme.bodySmall),
            TextButton(onPressed: _load, child: Text(rs.t('retry'))),
          ])));
    }

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('srvCfgTitle'))),
      body: ListView(padding: const EdgeInsets.all(12), children: [
        _cfgCard(theme, rs.t('srvCfgLlm'), _llm, 'llm'),
        const SizedBox(height: 12),
        _cfgCard(theme, rs.t('srvCfgSpeech'), _speech, 'speech'),
        const SizedBox(height: 12),
        _cfgCard(theme, rs.t('srvCfgVlm'), _vlm, 'vlm'),
        const SizedBox(height: 12),
        _cfgCard(theme, rs.t('srvCfgImageGen'), _imageGen, 'image_gen'),
        const SizedBox(height: 14),
        _section(rs.t('srvCfgTasks'), theme),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(children: [
              DropdownButtonFormField<String>(
                initialValue: _selectedTask,
                items: [
                  for (final t in _tasks)
                    DropdownMenuItem(
                        value: t['task'] as String,
                        child: Text('${t['name'] ?? ''} · ${t['task'] ?? ''}')),
                ],
                onChanged: (v) => setState(() => _selectedTask = v ?? ''),
                decoration: InputDecoration(
                    labelText: rs.t('srvCfgTaskSelect'),
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(8))),
              ),
              if (_selectedTask.isNotEmpty && _taskCfgs[_selectedTask] != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                      '${rs.t('srvCfgTaskModel')}: ${_taskCfgs[_selectedTask]!['model'] ?? '—'} · '
                      '${rs.t('srvCfgTaskEnabled')}: ${_taskCfgs[_selectedTask]!['enabled']}',
                      style: theme.textTheme.bodySmall),
                ),
            ]),
          ),
        ),
        if (_savedKey.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('✅ $_savedKey',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.primary)),
          ),
      ]),
    );
  }

  Widget _cfgCard(ThemeData theme, String title, _CfgSection cfg, String key) {
    final rs = widget.rs;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(title, style: theme.textTheme.titleSmall),
            const Spacer(),
            Switch(
                value: cfg.enabled,
                onChanged: (v) {
                  setState(() => cfg.enabled = v);
                  _save(key, cfg.toBody());
                }),
          ]),
          Text('${rs.t('srvCfgHasKey')}: ${cfg.hasApiKey ? '✓' : '—'}',
              style: theme.textTheme.labelSmall),
          const SizedBox(height: 8),
          TextField(
            controller: TextEditingController(text: cfg.baseUrl),
            onChanged: (v) => cfg.baseUrl = v,
            decoration: InputDecoration(
                labelText: rs.t('cfgBaseUrl'),
                isDense: true,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8))),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: TextEditingController(text: cfg.model),
            onChanged: (v) => cfg.model = v,
            decoration: InputDecoration(
                labelText: rs.t('cfgModel'),
                isDense: true,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8))),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: TextEditingController(text: cfg.provider),
            onChanged: (v) => cfg.provider = v,
            decoration: InputDecoration(
                labelText: rs.t('cfgProvider'),
                isDense: true,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8))),
          ),
          if (key == 'image_gen') ...[
            const SizedBox(height: 8),
            TextField(
              controller: TextEditingController(text: cfg.dailyLimit),
              onChanged: (v) => cfg.dailyLimit = v,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                  labelText: rs.t('srvCfgDailyLimit'),
                  isDense: true,
                  border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8))),
            ),
          ],
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: _saving
                  ? null
                  : () => _save(key, {
                        'enabled': cfg.enabled,
                        'base_url': cfg.baseUrl,
                        'model': cfg.model,
                        'provider': cfg.provider,
                        if (key == 'image_gen')
                          'daily_limit': int.tryParse(cfg.dailyLimit) ?? 10,
                      }),
              icon: _saving
                  ? const SizedBox(width: 14, height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.save_outlined, size: 16),
              label: Text(rs.t('cfgSave')),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _section(String title, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
        child: Text(title,
            style: theme.textTheme.titleSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      );
}
