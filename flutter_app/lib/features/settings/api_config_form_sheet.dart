// F7-c-6（2026-09-01）自 features/settings/api_config_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
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

class LlmConfigFormSheet extends StatefulWidget {
  final Map<String, dynamic>? existing;
  final Future<void> Function(Map<String, dynamic> body) onTest;
  final Future<void> Function(Map<String, dynamic>) onSaved;
  const LlmConfigFormSheet({super.key, this.existing, required this.onTest, required this.onSaved});

  @override
  State<LlmConfigFormSheet> createState() => LlmConfigFormSheetState();
}

class LlmConfigFormSheetState extends State<LlmConfigFormSheet> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _baseUrlCtrl;
  late final TextEditingController _modelCtrl;
  late final TextEditingController _providerCtrl;
  late final TextEditingController _apiKeyCtrl;
  bool _enabled = true;
  bool _isDefault = false;
  bool _sharedWithSubs = false;
  bool _saving = false;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final c = widget.existing;
    _nameCtrl = TextEditingController(text: c?['name']?.toString() ?? '');
    _baseUrlCtrl = TextEditingController(text: c?['base_url']?.toString() ?? '');
    _modelCtrl = TextEditingController(text: c?['model']?.toString() ?? '');
    _providerCtrl = TextEditingController(text: c?['provider']?.toString() ?? '');
    _apiKeyCtrl = TextEditingController(text: '');
    _enabled = c?['enabled'] as bool? ?? true;
    _isDefault = c?['is_default'] as bool? ?? false;
    _sharedWithSubs = c?['shared_with_subs'] as bool? ?? false;
  }

  @override
  void dispose() {
    _nameCtrl.dispose(); _baseUrlCtrl.dispose(); _modelCtrl.dispose();
    _providerCtrl.dispose(); _apiKeyCtrl.dispose();
    super.dispose();
  }

  void _applyPreset(Map<String, String> p) {
    setState(() {
      _providerCtrl.text = p['provider'] ?? '';
      _baseUrlCtrl.text = p['base_url'] ?? '';
      _modelCtrl.text = p['model'] ?? '';
    });
  }

  Map<String, dynamic> _body() => {
        'name': _nameCtrl.text.trim(),
        'base_url': _baseUrlCtrl.text.trim(),
        'model': _modelCtrl.text.trim(),
        'provider': _providerCtrl.text.trim(),
        'enabled': _enabled,
        'is_default': _isDefault,
        'shared_with_subs': _sharedWithSubs,
        if (_apiKeyCtrl.text.trim().isNotEmpty) 'api_key': _apiKeyCtrl.text.trim(),
      };

  Future<void> _test() async {
    if (_baseUrlCtrl.text.trim().isEmpty || _apiKeyCtrl.text.trim().isEmpty) {
      return;
    }
    await widget.onTest({
      'base_url': _baseUrlCtrl.text.trim(),
      'api_key': _apiKeyCtrl.text.trim(),
      'model': _modelCtrl.text.trim(),
      'provider': _providerCtrl.text.trim(),
    });
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    if (_nameCtrl.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.llmConfigNameRequired)));
      return;
    }
    setState(() => _saving = true);
    try {
      final api = ApiClient();
      if (_isEdit) {
        await api.updateLlmConfig(widget.existing!['id'] as int, _body());
      } else {
        await api.createLlmConfig(_body());
      }
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e))));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    Widget field(TextEditingController ctrl, String label, {bool obscure = false, String? hint}) {
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: TextField(
          controller: ctrl,
          obscureText: obscure,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            isDense: true,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide.none,
            ),
            filled: true,
            fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
          ),
        ),
      );
    }

    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
          left: 12, right: 12,
          top: 12,
          bottom: MediaQuery.of(context).viewInsets.bottom + 16,
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Text(_isEdit ? l10n.editLlmConfig : l10n.newLlmConfig,
                      style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                  const Spacer(),
                  IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.pop(context)),
                ],
              ),
              field(_nameCtrl, l10n.llmConfigName),
              DropdownButtonFormField<String>(
                initialValue: null,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: l10n.llmPresets,
                  hintText: l10n.presetSelectHint,
                  isDense: true,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
                ),
                items: [
                  for (final e in kLlmPresets.entries)
                    DropdownMenuItem(value: e.key, child: Text(e.key)),
                ],
                onChanged: (v) {
                  if (v != null) _applyPreset(kLlmPresets[v]!);
                },
              ),
              field(_baseUrlCtrl, 'Base URL', hint: 'https://api.deepseek.com/v1'),
              field(_modelCtrl, l10n.model, hint: 'deepseek-chat'),
              field(_providerCtrl, l10n.provider,
                  hint: 'deepseek / dashscope / openai / zhipu / kimi / siliconflow'),
              field(_apiKeyCtrl, l10n.apiKeyKeep, obscure: true,
                  hint: widget.existing?['has_api_key'] == true ? l10n.apiKeyHintReplace : 'sk-...'),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.enable, style: const TextStyle(fontSize: 13)),
                value: _enabled,
                onChanged: (v) => setState(() => _enabled = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.setDefault, style: const TextStyle(fontSize: 13)),
                value: _isDefault,
                onChanged: (v) => setState(() => _isDefault = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.sharedWithSubs, style: const TextStyle(fontSize: 13)),
                value: _sharedWithSubs,
                onChanged: (v) => setState(() => _sharedWithSubs = v),
              ),
              Row(children: [
                Expanded(child: OutlinedButton(onPressed: _test, child: Text(l10n.testConnection))),
                const SizedBox(width: 8),
                Expanded(
                  child: FilledButton(
                    onPressed: _saving ? null : _save,
                    child: _saving
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : Text(l10n.save),
                  ),
                ),
              ]),
            ],
          ),
        ),
      ),
    );
  }
}


/// LLM 用量统计弹窗：总额/已用/剩余 + 今日/近7天/本月 + 按模型（入口：API 配置右上角）
