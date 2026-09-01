// F7-c-4c（2026-08-31）自 screens/plugin/extensions_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';

/// 扩展（插件）页：分类列表 / 启用开关 / 参数配置 / zip 安装（仅主账号）
class PluginConfigForm extends StatefulWidget {
  const PluginConfigForm({
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
  State<PluginConfigForm> createState() => PluginConfigFormState();
}

class PluginConfigFormState extends State<PluginConfigForm> {
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
class ZeroCodeConfigEditor extends StatefulWidget {
  const ZeroCodeConfigEditor({
    super.key,
    required this.plugin,
    required this.onToast,
    required this.onSaved,
  });

  final Map<String, dynamic> plugin;
  final void Function(String) onToast;
  final VoidCallback onSaved;

  @override
  State<ZeroCodeConfigEditor> createState() => ZeroCodeConfigEditorState();
}

class ZeroCodeConfigEditorState extends State<ZeroCodeConfigEditor> {
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
