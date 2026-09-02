import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../features/settings/api_config_form_sheet.dart' show LlmConfigFormSheet, kLlmPresets;
import '../../features/settings/api_config_usage_sheet.dart';

/// 常用供应商预设（19：选中自动填入 base_url/model/provider，Key 仍需手动填）

const Map<String, Map<String, String>> kSpeechPresets = {
  'OpenAI Whisper': {'provider': 'openai', 'base_url': 'https://api.openai.com/v1', 'model': 'whisper-1'},
  '阿里百炼 Paraformer': {
    'provider': 'dashscope',
    'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'model': 'paraformer-realtime-v2',
  },
  '智谱 GLM-4-Voice': {'provider': 'zhipu', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4-voice'},
};

const Map<String, Map<String, String>> kVlmPresets = {
  '阿里百炼 Qwen-VL': {'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-vl-plus'},
  'OpenAI GPT-4o': {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'},
  '硅基流动 Qwen2.5-VL': {'base_url': 'https://api.siliconflow.cn/v1', 'model': 'Qwen/Qwen2.5-VL-7B-Instruct'},
};

const Map<String, Map<String, String>> kImagePresets = {
  '阿里百炼 Qwen-Image': {
    'provider': 'dashscope',
    'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'model': 'qwen-image-3.0',
  },
  'OpenAI DALL·E': {'provider': 'openai', 'base_url': 'https://api.openai.com/v1', 'model': 'dall-e-3'},
  '硅基流动 Kolors': {'provider': 'openai', 'base_url': 'https://api.siliconflow.cn/v1', 'model': 'Kwai-Kolors/Kolors'},
};

/// API 配置页：#68 P1 改 Tab（LLM / 语音 / 识图 / 生图 / 任务）。
/// LLM Tab = 服务器级 LLM（仅主账号）+ 我的 LLM 列表（新建/编辑/删除/测试/设默认/共享开关）+ 主账号共享（子账号只读）+ 用量入口。
/// 全模态 Tab 与后端 multimodal-config 接口均已清理（#68 P7）。
class ApiConfigScreen extends StatefulWidget {
  const ApiConfigScreen({super.key});

  @override
  State<ApiConfigScreen> createState() => _ApiConfigScreenState();
}

class _ApiConfigScreenState extends State<ApiConfigScreen> {
  bool _loading = true;
  bool _isAdmin = false;

  // ── 我的 LLM（多配置列表）──
  List<Map<String, dynamic>> _llmConfigs = [];

  // 服务器级 LLM
  bool _srvEnabled = false;
  bool _srvHasKey = false;
  final _srvBaseUrl = TextEditingController();
  final _srvApiKey = TextEditingController();
  final _srvModel = TextEditingController();
  final _srvProvider = TextEditingController();

  // 服务器级语音大模型（当前转写仍走本地 whisper，云端配置先占位落库）
  bool _spEnabled = false;
  bool _spHasKey = false;
  final _spProvider = TextEditingController();
  final _spBaseUrl = TextEditingController();
  final _spApiKey = TextEditingController();
  final _spModel = TextEditingController();

  // 服务器级识图（图片理解）
  bool _vlmEnabled = false;
  bool _vlmHasKey = false;
  final _vlmBaseUrl = TextEditingController();
  final _vlmApiKey = TextEditingController();
  final _vlmModel = TextEditingController();

  // 服务器级生图
  bool _imgEnabled = false;
  bool _imgHasKey = false;
  final _imgProvider = TextEditingController();
  final _imgBaseUrl = TextEditingController();
  final _imgApiKey = TextEditingController();
  final _imgModel = TextEditingController();
  final _imgDailyLimit = TextEditingController();

  // 任务专用模型（按用途指定；服务器级，仅主账号）
  List<Map<String, dynamic>> _taskList = [];
  String _task = 'memory';
  bool _taskEnabled = false;
  bool _taskHasKey = false;
  final _taskProvider = TextEditingController();
  final _taskBaseUrl = TextEditingController();
  final _taskApiKey = TextEditingController();
  final _taskModel = TextEditingController();

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().isAdmin;
    _load();
  }

  @override
  void dispose() {
    _srvBaseUrl.dispose(); _srvApiKey.dispose(); _srvModel.dispose(); _srvProvider.dispose();
    _spProvider.dispose(); _spBaseUrl.dispose(); _spApiKey.dispose(); _spModel.dispose();
    _vlmBaseUrl.dispose(); _vlmApiKey.dispose(); _vlmModel.dispose();
    _imgProvider.dispose(); _imgBaseUrl.dispose(); _imgApiKey.dispose();
    _imgModel.dispose(); _imgDailyLimit.dispose();
    _taskProvider.dispose(); _taskBaseUrl.dispose(); _taskApiKey.dispose(); _taskModel.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _loading = true);
    try {
      final api = ApiClient();
      // 我的 LLM 列表
      await _loadLlmConfigs(api);
      if (_isAdmin) {
        final srv = await api.getServerApiConfig();
      if (!mounted) return;
        setState(() {
          _srvEnabled = srv['enabled'] as bool? ?? false;
          _srvHasKey = srv['has_api_key'] as bool? ?? false;
          _srvBaseUrl.text = srv['base_url'] as String? ?? '';
          _srvModel.text = srv['model'] as String? ?? '';
          _srvProvider.text = srv['provider'] as String? ?? '';
        });
        final sp = await api.getSpeechServerConfig();
      if (!mounted) return;
        setState(() {
          _spEnabled = sp['enabled'] as bool? ?? false;
          _spHasKey = sp['has_api_key'] as bool? ?? false;
          _spProvider.text = sp['provider'] as String? ?? '';
          _spBaseUrl.text = sp['base_url'] as String? ?? '';
          _spModel.text = sp['model'] as String? ?? '';
        });
        final vlm = await api.getVlmServerConfig();
      if (!mounted) return;
        setState(() {
          _vlmEnabled = vlm['enabled'] as bool? ?? false;
          _vlmHasKey = vlm['has_api_key'] as bool? ?? false;
          _vlmBaseUrl.text = vlm['base_url'] as String? ?? '';
          _vlmModel.text = vlm['model'] as String? ?? '';
        });
        final img = await api.getImageGenServerConfig();
      if (!mounted) return;
        setState(() {
          _imgEnabled = img['enabled'] as bool? ?? false;
          _imgHasKey = img['has_api_key'] as bool? ?? false;
          _imgProvider.text = img['provider'] as String? ?? '';
          _imgBaseUrl.text = img['base_url'] as String? ?? '';
          _imgModel.text = img['model'] as String? ?? '';
          _imgDailyLimit.text = (img['daily_limit'] as num? ?? 10).toString();
        });
        final taskList = await api.getTaskLlmCatalog();
      if (!mounted) return;
        setState(() {
          _taskList = taskList;
          if (_taskList.isNotEmpty) _task = _taskList.first['task'] as String;
        });
        await _loadTaskConfig();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.loadConfigFailed(e))),
        );
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadLlmConfigs([ApiClient? api]) async {
    try {
      final list = await (api ?? ApiClient()).listLlmConfigs();
      if (mounted) setState(() => _llmConfigs = list);
    } catch (_) {
      if (mounted) setState(() => _llmConfigs = []);
    }
  }

  Map<String, dynamic> _srvBody() => {
        'enabled': _srvEnabled,
        'base_url': _srvBaseUrl.text.trim(),
        'model': _srvModel.text.trim(),
        'provider': _srvProvider.text.trim(),
        if (_srvApiKey.text.trim().isNotEmpty) 'api_key': _srvApiKey.text.trim(),
      };

  Map<String, dynamic> _spBody() => {
        'enabled': _spEnabled,
        'provider': _spProvider.text.trim(),
        'base_url': _spBaseUrl.text.trim(),
        'model': _spModel.text.trim(),
        if (_spApiKey.text.trim().isNotEmpty) 'api_key': _spApiKey.text.trim(),
      };

  Map<String, dynamic> _vlmBody() => {
        'enabled': _vlmEnabled,
        'base_url': _vlmBaseUrl.text.trim(),
        'model': _vlmModel.text.trim(),
        if (_vlmApiKey.text.trim().isNotEmpty) 'api_key': _vlmApiKey.text.trim(),
      };

  Map<String, dynamic> _imgBody() => {
        'enabled': _imgEnabled,
        'provider': _imgProvider.text.trim(),
        'base_url': _imgBaseUrl.text.trim(),
        'model': _imgModel.text.trim(),
        'daily_limit': int.tryParse(_imgDailyLimit.text.trim()) ?? 10,
        if (_imgApiKey.text.trim().isNotEmpty) 'api_key': _imgApiKey.text.trim(),
      };

  Future<void> _loadTaskConfig() async {
    try {
      final c = await ApiClient().getServerTaskApiConfig(_task);
      if (mounted) {
        setState(() {
          _taskEnabled = c['enabled'] as bool? ?? false;
          _taskHasKey = c['has_api_key'] as bool? ?? false;
          _taskBaseUrl.text = c['base_url'] as String? ?? '';
          _taskModel.text = c['model'] as String? ?? '';
          _taskProvider.text = c['provider'] as String? ?? '';
        });
      }
    } catch (_) {}
  }

  Map<String, dynamic> _taskBody() => {
        'enabled': _taskEnabled,
        'base_url': _taskBaseUrl.text.trim(),
        'model': _taskModel.text.trim(),
        'provider': _taskProvider.text.trim(),
        if (_taskApiKey.text.trim().isNotEmpty) 'api_key': _taskApiKey.text.trim(),
      };

  Future<void> _testConnection(Map<String, dynamic> body) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await ApiClient().testApiConnection(body);
      if (!mounted) return;
      final ok = r['ok'] == true;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(ok
            ? l10n.connSuccess(r['latency_ms'], r['model'], r['api_key_tail'])
            : l10n.connFailed(r['error'] ?? l10n.unknown)),
        backgroundColor: ok ? null : Theme.of(context).colorScheme.error,
      ));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.testRequestFailed(e))),
        );
      }
    }
  }

  Widget _outlineButton(String text, VoidCallback onPressed) {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: SizedBox(
        width: double.infinity,
        child: OutlinedButton(onPressed: onPressed, child: Text(text)),
      ),
    );
  }

  /// 任务选择下拉：切换任务时重新加载该任务的服务器级配置
  Widget _taskDropdown() {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: DropdownButtonFormField<String>(
        initialValue: _taskList.any((t) => t['task'] == _task) ? _task : null,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: l10n.task,
          hintText: l10n.taskHint,
          isDense: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
        ),
        items: [
          for (final t in _taskList)
            DropdownMenuItem(
              value: t['task'] as String,
              child: Text('${t['name']}（${t['task']}）'),
            ),
        ],
        onChanged: (v) async {
          if (v == null) return;
          setState(() => _task = v);
          await _loadTaskConfig();
        },
      ),
    );
  }

  Future<void> _save(Future<Map<String, dynamic>> Function() action) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await action();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.saveSuccessEnabled(r['enabled']))),
        );
        await _load();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.saveFailedErr(e))),
        );
      }
    }
  }

  Widget _section(String title, String subtitle, List<Widget> children) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 2),
            Text(subtitle, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            const SizedBox(height: 8),
            ...children,
          ],
        ),
      ),
    );
  }

  Widget _field(TextEditingController ctrl, String label,
      {bool obscure = false, String? hint, bool enabled = true}) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: TextField(
        controller: ctrl,
        obscureText: obscure,
        enabled: enabled,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          isDense: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
        ),
      ),
    );
  }

  /// 供应商预设下拉（19：选中自动填入 base_url/model/provider）
  Widget _presetDropdown(
    String label,
    Map<String, Map<String, String>> presets,
    void Function(Map<String, String> preset) onApply,
  ) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: DropdownButtonFormField<String>(
        initialValue: null,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: label,
          hintText: l10n.presetSelectHint,
          isDense: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
        ),
        items: [
          for (final e in presets.entries)
            DropdownMenuItem(value: e.key, child: Text(e.key)),
        ],
        onChanged: (v) {
          if (v != null) onApply(presets[v]!);
        },
      ),
    );
  }

  Widget _saveButton(String text, Future<Map<String, dynamic>> Function() action) {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: SizedBox(
        width: double.infinity,
        child: FilledButton(onPressed: () => _save(action), child: Text(text)),
      ),
    );
  }

  void _openUsage() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      isScrollControlled: true,
      builder: (_) => const LlmUsageSheet(),
    );
  }

  // ── 我的 LLM 列表（P0/P1）──

  Future<void> _openLlmConfigForm([Map<String, dynamic>? existing]) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      isScrollControlled: true,
      builder: (_) => LlmConfigFormSheet(
        existing: existing,
        onTest: _testConnection,
        onSaved: (created) async {
          await _loadLlmConfigs();
        },
      ),
    );
    if (saved == true) await _loadLlmConfigs();
  }

  Future<void> _deleteLlmConfig(Map<String, dynamic> c) async {
    final l10n = AppLocalizations.of(context)!;
    final id = c['id'] as int;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.delete),
        content: Text(l10n.deleteFriendConfirm(c['name']?.toString() ?? '')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (confirm != true || !mounted) return;
    try {
      await ApiClient().deleteLlmConfig(id);
      await _loadLlmConfigs();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveSuccessEnabled(true))));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e))));
      }
    }
  }

  Future<void> _setDefaultLlmConfig(Map<String, dynamic> c) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await ApiClient().setLlmConfigDefault(c['id'] as int);
      await _loadLlmConfigs();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveSuccessEnabled(true))));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e))));
      }
    }
  }

  Future<void> _toggleLlmShare(Map<String, dynamic> c, bool val) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await ApiClient().setLlmConfigShare(c['id'] as int, val);
      await _loadLlmConfigs();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e))));
      }
    }
  }

  Future<void> _testLlmConfig(Map<String, dynamic> c) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await ApiClient().testLlmConfig(c['id'] as int);
      if (!mounted) return;
      final ok = r['ok'] == true;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(ok
            ? l10n.connSuccess(r['latency_ms'], r['model'], r['api_key_tail'])
            : l10n.connFailed(r['error'] ?? l10n.unknown)),
        backgroundColor: ok ? null : Theme.of(context).colorScheme.error,
      ));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.testRequestFailed(e))));
      }
    }
  }

  // ── LLM Tab 构建 ──
  Widget _buildLlmTab() {
    final l10n = AppLocalizations.of(context)!;
    final own = _llmConfigs.where((c) => c['is_shared'] != true).toList();
    final shared = _llmConfigs.where((c) => c['is_shared'] == true).toList();
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        if (_isAdmin) ...[
          _section(
            l10n.srvLlm,
            l10n.srvLlmHint,
            [
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.enable),
                subtitle: Text(_srvHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
                value: _srvEnabled,
                onChanged: (v) => setState(() => _srvEnabled = v),
              ),
              _presetDropdown(l10n.llmPresets, kLlmPresets, (p) {
                setState(() {
                  _srvBaseUrl.text = p['base_url'] ?? '';
                  _srvModel.text = p['model'] ?? '';
                  _srvProvider.text = p['provider'] ?? '';
                });
              }),
              _field(_srvBaseUrl, 'Base URL', enabled: _srvEnabled),
              _field(_srvModel, l10n.model, enabled: _srvEnabled),
              _field(_srvProvider, l10n.provider, hint: 'deepseek / dashscope / openai / zhipu / kimi / siliconflow', enabled: _srvEnabled),
              _field(_srvApiKey, l10n.apiKeyKeep, obscure: true, enabled: _srvEnabled,
                  hint: l10n.apiKeyRotateHint),
              Row(children: [
                Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_srvBody()))),
                const SizedBox(width: 8),
                Expanded(child: _saveButton(l10n.saveSrvLlm, () => ApiClient().updateServerApiConfig(_srvBody()))),
              ]),
            ],
          ),
        ] else
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              l10n.srvAdminOnly,
              style: TextStyle(color: Colors.grey[600]),
            ),
          ),
        // 我的 LLM
        _section(
          l10n.myLlm,
          l10n.myLlmHint,
          [
            if (own.isEmpty)
              Padding(
                padding: const EdgeInsets.all(14),
                child: Text(l10n.emptyLlmConfigs,
                    style: TextStyle(color: IosCardColors.subtitle)),
              )
            else
              ...own.map((c) => _llmConfigCard(c)),
            _saveButton(l10n.newLlmConfig, () async {
              await _openLlmConfigForm();
              return {'enabled': false};
            }),
          ],
        ),
        // 主账号共享（子账号只读）
        if (shared.isNotEmpty)
          _section(l10n.sharedConfigList, l10n.llmSharedReadonly,
              shared.map((c) => _sharedConfigCard(c)).toList()),
        const SizedBox(height: 8),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: TextButton.icon(
            onPressed: _openUsage,
            icon: const Icon(Icons.insights_outlined),
            label: Text(l10n.usageStats),
          ),
        ),
      ],
    );
  }

  Widget _llmConfigCard(Map<String, dynamic> c) {
    final l10n = AppLocalizations.of(context)!;
    final badges = <Widget>[
      if (c['is_default'] == true)
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(l10n.defaultBadge,
              style: TextStyle(fontSize: 11, color: Theme.of(context).colorScheme.primary)),
        ),
      if (c['shared_with_subs'] == true)
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: const Color(0xFFE8F0FE),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(l10n.sharedBadge, style: const TextStyle(fontSize: 11, color: Color(0xFF1967D2))),
        ),
    ];
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(c['name']?.toString() ?? '',
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ),
                ...badges.map((b) => Padding(padding: const EdgeInsets.only(left: 6), child: b)),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              [c['provider'], c['model']].whereType<String>().where((s) => s.isNotEmpty).join(' · '),
              style: TextStyle(fontSize: 12, color: IosCardColors.subtitle),
            ),
            const Divider(height: 16),
            Row(
              children: [
                TextButton.icon(
                  onPressed: () => _testLlmConfig(c),
                  icon: const Icon(Icons.wifi_tethering, size: 16),
                  label: Text(l10n.testConnection),
                ),
                TextButton.icon(
                  onPressed: () => _openLlmConfigForm(c),
                  icon: const Icon(Icons.edit_outlined, size: 16),
                  label: Text(l10n.editLlmConfig),
                ),
                TextButton.icon(
                  onPressed: () => _setDefaultLlmConfig(c),
                  icon: const Icon(Icons.star_outline, size: 16),
                  label: Text(l10n.setDefault),
                ),
                const Spacer(),
                IconButton(
                  tooltip: l10n.delete,
                  icon: const Icon(Icons.delete_outline, size: 18),
                  onPressed: () => _deleteLlmConfig(c),
                ),
              ],
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              title: Text(l10n.sharedWithSubs, style: const TextStyle(fontSize: 12)),
              value: c['shared_with_subs'] == true,
              onChanged: (v) => _toggleLlmShare(c, v),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sharedConfigCard(Map<String, dynamic> c) {
    final l10n = AppLocalizations.of(context)!;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(c['name']?.toString() ?? '',
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: const Color(0xFFE8F0FE),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(l10n.sharedBadge, style: const TextStyle(fontSize: 11, color: Color(0xFF1967D2))),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              [c['provider'], c['model']].whereType<String>().where((s) => s.isNotEmpty).join(' · '),
              style: TextStyle(fontSize: 12, color: IosCardColors.subtitle),
            ),
            const SizedBox(height: 4),
            Row(
              children: [
                Icon(Icons.lock_outline, size: 12, color: IosCardColors.subtitle),
                const SizedBox(width: 4),
                Text(c['has_api_key'] == true ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort,
                    style: TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          title: Text(l10n.apiConfig),
          actions: [
            IconButton(
              icon: const Icon(Icons.insights_outlined),
              tooltip: l10n.usageStats,
              onPressed: _openUsage,
            ),
          ],
          bottom: _loading
              ? null
              : TabBar(
                  isScrollable: true,
                  tabs: [
                    Tab(text: l10n.apiTabLlm),
                    Tab(text: l10n.apiTabSpeech),
                    Tab(text: l10n.apiTabVision),
                    Tab(text: l10n.apiTabImage),
                    Tab(text: l10n.apiTabTask),
                  ],
                ),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : TabBarView(
                children: [
                  _buildLlmTab(),
                  _buildSpeechTab(),
                  _buildVlmTab(),
                  _buildImageTab(),
                  _buildTaskTab(),
                ],
              ),
      ),
    );
  }

  Widget _buildSpeechTab() {
    final l10n = AppLocalizations.of(context)!;
    if (!_isAdmin) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(l10n.srvAdminOnly, style: TextStyle(color: Colors.grey[600])),
        ),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _section(
          l10n.srvSpeech,
          l10n.srvSpeechHint,
          [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.enable),
              subtitle: Text(_spHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
              value: _spEnabled,
              onChanged: (v) => setState(() => _spEnabled = v),
            ),
            _presetDropdown(l10n.speechPresets, kSpeechPresets, (p) {
              setState(() {
                _spProvider.text = p['provider'] ?? '';
                _spBaseUrl.text = p['base_url'] ?? '';
                _spModel.text = p['model'] ?? '';
              });
            }),
            _field(_spProvider, 'Provider', enabled: _spEnabled),
            _field(_spBaseUrl, 'Base URL', enabled: _spEnabled),
            _field(_spModel, l10n.model, enabled: _spEnabled),
            _field(_spApiKey, l10n.apiKeyKeep, obscure: true, enabled: _spEnabled),
            Row(children: [
              Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_spBody()))),
              const SizedBox(width: 8),
              Expanded(child: _saveButton(l10n.save, () => ApiClient().updateSpeechServerConfig(_spBody()))),
            ]),
          ],
        ),
      ],
    );
  }

  Widget _buildVlmTab() {
    final l10n = AppLocalizations.of(context)!;
    if (!_isAdmin) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(l10n.srvAdminOnly, style: TextStyle(color: Colors.grey[600])),
        ),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _section(
          l10n.srvVlm,
          l10n.srvVlmHint,
          [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.enable),
              subtitle: Text(_vlmHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
              value: _vlmEnabled,
              onChanged: (v) => setState(() => _vlmEnabled = v),
            ),
            _presetDropdown(l10n.vlmPresets, kVlmPresets, (p) {
              setState(() {
                _vlmBaseUrl.text = p['base_url'] ?? '';
                _vlmModel.text = p['model'] ?? '';
              });
            }),
            _field(_vlmBaseUrl, 'Base URL', enabled: _vlmEnabled),
            _field(_vlmModel, l10n.model, enabled: _vlmEnabled),
            _field(_vlmApiKey, l10n.apiKeyKeep, obscure: true, enabled: _vlmEnabled),
            Row(children: [
              Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_vlmBody()))),
              const SizedBox(width: 8),
              Expanded(child: _saveButton(l10n.save, () => ApiClient().updateVlmServerConfig(_vlmBody()))),
            ]),
          ],
        ),
      ],
    );
  }

  Widget _buildImageTab() {
    final l10n = AppLocalizations.of(context)!;
    if (!_isAdmin) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(l10n.srvAdminOnly, style: TextStyle(color: Colors.grey[600])),
        ),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _section(
          l10n.srvImageGen,
          l10n.srvImageGenHint,
          [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.enable),
              subtitle: Text(_imgHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
              value: _imgEnabled,
              onChanged: (v) => setState(() => _imgEnabled = v),
            ),
            _presetDropdown(l10n.imagePresets, kImagePresets, (p) {
              setState(() {
                _imgProvider.text = p['provider'] ?? '';
                _imgBaseUrl.text = p['base_url'] ?? '';
                _imgModel.text = p['model'] ?? '';
              });
            }),
            _field(_imgProvider, 'Provider', enabled: _imgEnabled),
            _field(_imgBaseUrl, 'Base URL', enabled: _imgEnabled),
            _field(_imgModel, l10n.model, enabled: _imgEnabled),
            _field(_imgApiKey, l10n.apiKeyKeep, obscure: true, enabled: _imgEnabled),
            _field(_imgDailyLimit, l10n.dailyLimit, enabled: _imgEnabled),
            Row(children: [
              Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_imgBody()))),
              const SizedBox(width: 8),
              Expanded(child: _saveButton(l10n.save, () => ApiClient().updateImageGenServerConfig(_imgBody()))),
            ]),
          ],
        ),
      ],
    );
  }

  Widget _buildTaskTab() {
    final l10n = AppLocalizations.of(context)!;
    if (!_isAdmin) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(l10n.srvAdminOnly, style: TextStyle(color: Colors.grey[600])),
        ),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _section(
          l10n.srvTask,
          l10n.srvTaskHint,
          [
            _taskDropdown(),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l10n.enable),
              subtitle: Text(_taskHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
              value: _taskEnabled,
              onChanged: (v) => setState(() => _taskEnabled = v),
            ),
            _field(_taskProvider, l10n.provider, enabled: _taskEnabled),
            _field(_taskBaseUrl, 'Base URL', enabled: _taskEnabled),
            _field(_taskModel, l10n.model, enabled: _taskEnabled),
            _field(_taskApiKey, l10n.apiKeyKeep, obscure: true, enabled: _taskEnabled),
            Row(children: [
              Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_taskBody()))),
              const SizedBox(width: 8),
              Expanded(child: _saveButton(l10n.save, () => ApiClient().updateServerTaskApiConfig(_task, _taskBody()))),
            ]),
          ],
        ),
      ],
    );
  }
}


/// 我的 LLM 配置新建/编辑表单（#68 P1）
