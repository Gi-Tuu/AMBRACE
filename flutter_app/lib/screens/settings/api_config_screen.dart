import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 常用供应商预设（19：选中自动填入 base_url/model/provider，Key 仍需手动填）
const Map<String, Map<String, String>> kLlmPresets = {
  'DeepSeek': {'provider': 'deepseek', 'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'},
  '阿里百炼': {'provider': 'dashscope', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-plus'},
  '阿里百炼 Kimi-K2.6': {'provider': 'dashscope', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'kimi-k2.6'},
  '阿里百炼 Qwen-Plus-Character': {'provider': 'dashscope', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-plus-character'},
  'OpenAI': {'provider': 'openai', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'},
  '智谱 GLM': {'provider': 'zhipu', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4-flash'},
  'Moonshot Kimi': {'provider': 'kimi', 'base_url': 'https://api.moonshot.cn/v1', 'model': 'moonshot-v1-8k'},
  '硅基流动': {'provider': 'siliconflow', 'base_url': 'https://api.siliconflow.cn/v1', 'model': 'Qwen/Qwen2.5-7B-Instruct'},
};

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

const Map<String, Map<String, String>> kMultimodalPresets = {
  '阿里百炼 Qwen-VL-Max': {'provider': 'dashscope', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-vl-max'},
  'OpenAI GPT-4o': {'provider': 'openai', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o'},
  '智谱 GLM-4V': {'provider': 'zhipu', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4v-flash'},
};

/// API 配置页：用户级 BYOK（我的 LLM）+ 服务器级 LLM / 语音 / 识图 / 生图 / 全模态（仅主账号）
class ApiConfigScreen extends StatefulWidget {
  const ApiConfigScreen({super.key});

  @override
  State<ApiConfigScreen> createState() => _ApiConfigScreenState();
}

class _ApiConfigScreenState extends State<ApiConfigScreen> {
  bool _loading = true;
  bool _isAdmin = false;

  // 我的 LLM（BYOK）
  bool _myEnabled = false;
  bool _myHasKey = false;
  final _myBaseUrl = TextEditingController();
  final _myApiKey = TextEditingController();
  final _myModel = TextEditingController();
  final _myProvider = TextEditingController();

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

  // 服务器级全模态大模型
  bool _mmEnabled = false;
  bool _mmHasKey = false;
  final _mmProvider = TextEditingController();
  final _mmBaseUrl = TextEditingController();
  final _mmApiKey = TextEditingController();
  final _mmModel = TextEditingController();

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
    _myBaseUrl.dispose(); _myApiKey.dispose(); _myModel.dispose(); _myProvider.dispose();
    _srvBaseUrl.dispose(); _srvApiKey.dispose(); _srvModel.dispose(); _srvProvider.dispose();
    _spProvider.dispose(); _spBaseUrl.dispose(); _spApiKey.dispose(); _spModel.dispose();
    _vlmBaseUrl.dispose(); _vlmApiKey.dispose(); _vlmModel.dispose();
    _imgProvider.dispose(); _imgBaseUrl.dispose(); _imgApiKey.dispose();
    _imgModel.dispose(); _imgDailyLimit.dispose();
    _mmProvider.dispose(); _mmBaseUrl.dispose(); _mmApiKey.dispose(); _mmModel.dispose();
    _taskProvider.dispose(); _taskBaseUrl.dispose(); _taskApiKey.dispose(); _taskModel.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _loading = true);
    try {
      final api = ApiClient();
      final my = await api.getApiConfig();
      if (!mounted) return;
      setState(() {
        _myEnabled = my['enabled'] as bool? ?? false;
        _myHasKey = my['has_api_key'] as bool? ?? false;
        _myBaseUrl.text = my['base_url'] as String? ?? '';
        _myModel.text = my['model'] as String? ?? '';
        _myProvider.text = my['provider'] as String? ?? '';
      });
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
        final mm = await api.getMultimodalServerConfig();
      if (!mounted) return;
        setState(() {
          _mmEnabled = mm['enabled'] as bool? ?? false;
          _mmHasKey = mm['has_api_key'] as bool? ?? false;
          _mmProvider.text = mm['provider'] as String? ?? '';
          _mmBaseUrl.text = mm['base_url'] as String? ?? '';
          _mmModel.text = mm['model'] as String? ?? '';
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

  Map<String, dynamic> _myBody() => {
        'enabled': _myEnabled,
        'base_url': _myBaseUrl.text.trim(),
        'model': _myModel.text.trim(),
        'provider': _myProvider.text.trim(),
        if (_myApiKey.text.trim().isNotEmpty) 'api_key': _myApiKey.text.trim(),
      };

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

  Map<String, dynamic> _mmBody() => {
        'enabled': _mmEnabled,
        'provider': _mmProvider.text.trim(),
        'base_url': _mmBaseUrl.text.trim(),
        'model': _mmModel.text.trim(),
        if (_mmApiKey.text.trim().isNotEmpty) 'api_key': _mmApiKey.text.trim(),
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
        value: _taskList.any((t) => t['task'] == _task) ? _task : null,
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
        value: null,
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
      builder: (_) => const _LlmUsageSheet(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.apiConfig),
        actions: [
          IconButton(
            icon: const Icon(Icons.insights_outlined),
            tooltip: l10n.usageStats,
            onPressed: _openUsage,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              children: [
                _section(
                  l10n.myLlm,
                  l10n.myLlmHint,
                  [
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(l10n.enable),
                      subtitle: Text(_myHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfigured),
                      value: _myEnabled,
                      onChanged: (v) => setState(() => _myEnabled = v),
                    ),
                    _field(_myBaseUrl, 'Base URL', hint: 'https://api.deepseek.com/v1'),
                    _field(_myModel, l10n.model, hint: 'deepseek-chat'),
                    _field(_myProvider, l10n.provider, hint: 'deepseek / dashscope / openai / zhipu / kimi / siliconflow'),
                    _field(_myApiKey, l10n.apiKeyKeep, obscure: true,
                        hint: _myHasKey ? l10n.apiKeyHintReplace : 'sk-...'),
                    Row(children: [
                      Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_myBody()))),
                      const SizedBox(width: 8),
                      Expanded(child: _saveButton(l10n.saveMyConfig, () => ApiClient().updateApiConfig(_myBody()))),
                    ]),
                  ],
                ),
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
                      _field(_spProvider, 'Provider', hint: l10n.providerLocalHint, enabled: _spEnabled),
                      _field(_spBaseUrl, 'Base URL', hint: 'https://api.openai.com/v1', enabled: _spEnabled),
                      _field(_spModel, l10n.model, hint: 'whisper-1 / paraformer-realtime-v2', enabled: _spEnabled),
                      _field(_spApiKey, l10n.apiKeyKeep, obscure: true, enabled: _spEnabled),
                      _saveButton(l10n.saveSrvSpeech, () => ApiClient().updateSpeechServerConfig(_spBody())),
                    ],
                  ),
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
                      _field(_vlmBaseUrl, 'Base URL', hint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', enabled: _vlmEnabled),
                      _field(_vlmModel, l10n.model, hint: 'qwen-vl-plus / qwen2.5-vl-72b-instruct', enabled: _vlmEnabled),
                      _field(_vlmApiKey, l10n.apiKeyKeep, obscure: true, enabled: _vlmEnabled),
                      _saveButton(l10n.saveSrvVlm, () => ApiClient().updateVlmServerConfig(_vlmBody())),
                    ],
                  ),
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
                      _field(_imgProvider, 'Provider', hint: 'dashscope / openai', enabled: _imgEnabled),
                      _field(_imgBaseUrl, 'Base URL', enabled: _imgEnabled),
                      _field(_imgModel, l10n.model, hint: 'qwen-image-3.0', enabled: _imgEnabled),
                      _field(_imgApiKey, l10n.apiKeyKeep, obscure: true, enabled: _imgEnabled),
                      _field(_imgDailyLimit, l10n.dailyLimit, enabled: _imgEnabled),
                      _saveButton(l10n.saveSrvImageGen, () => ApiClient().updateImageGenServerConfig(_imgBody())),
                    ],
                  ),
                  _section(
                    l10n.srvMultimodal,
                    l10n.srvMultimodalHint,
                    [
                      SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(l10n.enable),
                        subtitle: Text(_mmHasKey ? l10n.apiKeyConfigured : l10n.apiKeyNotConfiguredShort),
                        value: _mmEnabled,
                        onChanged: (v) => setState(() => _mmEnabled = v),
                      ),
                      _presetDropdown(l10n.multimodalPresets, kMultimodalPresets, (p) {
                        setState(() {
                          _mmProvider.text = p['provider'] ?? '';
                          _mmBaseUrl.text = p['base_url'] ?? '';
                          _mmModel.text = p['model'] ?? '';
                        });
                      }),
                      _field(_mmProvider, 'Provider', hint: 'dashscope / openai / zhipu', enabled: _mmEnabled),
                      _field(_mmBaseUrl, 'Base URL', enabled: _mmEnabled),
                      _field(_mmModel, l10n.model, hint: 'qwen-vl-max / gpt-4o / glm-4v-flash', enabled: _mmEnabled),
                      _field(_mmApiKey, l10n.apiKeyKeep, obscure: true, enabled: _mmEnabled),
                      _saveButton(l10n.saveSrvMultimodal, () => ApiClient().updateMultimodalServerConfig(_mmBody())),
                    ],
                  ),
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
                      _field(_taskProvider, l10n.provider, hint: 'deepseek / dashscope / openai / zhipu / kimi / siliconflow', enabled: _taskEnabled),
                      _field(_taskBaseUrl, 'Base URL', enabled: _taskEnabled),
                      _field(_taskModel, l10n.model, enabled: _taskEnabled),
                      _field(_taskApiKey, l10n.apiKeyKeep, obscure: true, enabled: _taskEnabled),
                      Row(children: [
                        Expanded(child: _outlineButton(l10n.testConnection, () => _testConnection(_taskBody()))),
                        const SizedBox(width: 8),
                        Expanded(child: _saveButton(l10n.saveTaskConfig, () => ApiClient().updateServerTaskApiConfig(_task, _taskBody()))),
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
                const SizedBox(height: 20),
              ],
            ),
    );
  }
}


/// LLM 用量统计弹窗：总额/已用/剩余 + 今日/近7天/本月 + 按模型（入口：API 配置右上角）
class _LlmUsageSheet extends StatefulWidget {
  const _LlmUsageSheet();

  @override
  State<_LlmUsageSheet> createState() => _LlmUsageSheetState();
}

class _LlmUsageSheetState extends State<_LlmUsageSheet> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().getLlmUsage();
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
        _failed = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  Future<void> _setLimit() async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController(text: '${_data?['total_limit'] ?? 0}');
    final val = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.setQuotaTotal),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
            hintText: l10n.quotaHint,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide.none,
            ),
            filled: true,
            fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: Text(l10n.save),
          ),
        ],
      ),
    );
    if (val == null || val.isEmpty || !mounted) return;
    final limit = int.tryParse(val) ?? -1;
    if (limit < 0) return;
    try {
      await ApiClient().updateLlmUsageLimit(limit);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(limit == 0 ? l10n.quotaCleared : l10n.quotaUpdated)));
      _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.saveFailed)));
      }
    }
  }

  String _fmt(int n, AppLocalizations l10n) {
    if (n >= 100000000) return l10n.unitYi((n / 100000000).toStringAsFixed(1));
    if (n >= 10000) return l10n.unitWan((n / 10000).toStringAsFixed(1));
    return n.toString();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Text(l10n.llmUsageStats,
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(28),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_failed)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Text(l10n.loadFailedCheckServer,
                    textAlign: TextAlign.center,
                    style: TextStyle(color: IosCardColors.subtitle)),
              )
            else
              _buildContent(context, scheme),
          ],
        ),
      ),
    );
  }

  Widget _buildContent(BuildContext context, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final total = (_data?['used_total'] as num? ?? 0).toInt();
    final limit = (_data?['total_limit'] as num? ?? 0).toInt();
    final remaining = (_data?['remaining'] as num?)?.toInt();
    final today = (_data?['today'] as num? ?? 0).toInt();
    final week = (_data?['week'] as num? ?? 0).toInt();
    final month = (_data?['month'] as num? ?? 0).toInt();
    final byModel =
        (_data?['by_model'] as List? ?? []).cast<Map<String, dynamic>>();
    final canEdit = _data?['can_edit_limit'] == true;
    final cardColor = scheme.surfaceContainerHighest.withValues(alpha: 0.5);

    Widget statCard(String label, int value) => Expanded(
          child: Container(
            margin: const EdgeInsets.only(right: 8),
            padding: const EdgeInsets.symmetric(vertical: 12),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              children: [
                Text(_fmt(value, l10n),
                    style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: scheme.onSurface)),
                const SizedBox(height: 2),
                Text(label,
                    style: const TextStyle(
                        fontSize: 11, color: IosCardColors.subtitle)),
              ],
            ),
          ),
        );

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // 总额 / 已用 / 剩余进度
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: cardColor,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(l10n.usedTotal,
                      style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                  const Spacer(),
                  if (canEdit)
                    InkWell(
                      onTap: _setLimit,
                      child: Row(
                        children: [
                          Icon(Icons.edit_outlined,
                              size: 14, color: scheme.primary),
                          const SizedBox(width: 2),
                          Text(l10n.setQuota,
                              style: TextStyle(fontSize: 12, color: scheme.primary)),
                        ],
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                limit > 0
                    ? '${_fmt(total, l10n)} / ${_fmt(limit, l10n)} tokens'
                    : l10n.totalTokensNoQuota(_fmt(total, l10n)),
                style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: scheme.onSurface),
              ),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: limit > 0 ? (total / limit).clamp(0.0, 1.0) : null,
                  minHeight: 8,
                  backgroundColor: scheme.surfaceContainerHighest,
                ),
              ),
              if (remaining != null) ...[
                const SizedBox(height: 6),
                Text(
                  l10n.remainingTokens(_fmt(remaining, l10n)),
                  style: TextStyle(
                    fontSize: 12,
                    color: remaining <= 0 ? scheme.error : IosCardColors.subtitle,
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            statCard(l10n.today, today),
            statCard(l10n.last7Days, week),
            statCard(l10n.thisMonth, month),
          ],
        ),
        if (byModel.isNotEmpty) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.byModelUsage,
                    style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                const SizedBox(height: 4),
                for (final m in byModel.take(6))
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            m['model'] as String? ?? l10n.unknown,
                            style: TextStyle(fontSize: 13, color: scheme.onSurface),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        Text(
                          _fmt((m['total'] as num? ?? 0).toInt(), l10n),
                          style: const TextStyle(
                              fontSize: 12, color: IosCardColors.subtitle),
                        ),
                      ],
                    ),
                  ),
                if (byModel.length > 6)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(l10n.etcModels('${byModel.length}'),
                        style: const TextStyle(
                            fontSize: 11, color: IosCardColors.subtitle)),
                  ),
              ],
            ),
          ),
        ],
        const SizedBox(height: 8),
        Text(
          l10n.usageNote,
          style: TextStyle(fontSize: 10, color: IosCardColors.subtitle),
        ),
      ],
    );
  }
}
