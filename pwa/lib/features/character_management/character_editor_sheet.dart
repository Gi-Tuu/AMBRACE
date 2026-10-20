// PWA M1a-5 · 角色创建/编辑表单（10 字段，基本/更多两组，zh/en 文案）。
//
// 纪律（HANDOFF §3/§4）：
// - 编辑模式先 GET 再填；创建模式空表单，打开表单**不发写请求**；
// - 保存中**全部输入 enabled:false** + 保存按钮禁用（Web 语义教训），失败恢复可编辑并保留原文；
// - dirty 时的取消/系统返回统一「继续编辑 / 放弃」；遮罩与拖拽默认禁用（show() 里写死），
//   不允许静默丢输入；
// - 开场白**手填**，留空即空，绝不调用 generate-greeting / 模型；
// - 角色名/简介等用户内容原文展示，不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/character_editor_strings.dart';
import 'character_editor_state.dart';

class CharacterEditorSheet extends StatefulWidget {
  const CharacterEditorSheet({
    super.key,
    required this.client,
    required this.strings,
    required this.authGen,
    required this.routeGen,
    required this.isStale,
    required this.onSaved,
    this.characterId,
    this.currentUserId,
    this.onUnauthorized,
    this.onVerify,
  });

  final AmbApiClient client;
  final CharacterEditorStrings strings;
  final int authGen;
  final int routeGen;
  final StaleCheck isStale;
  final int? characterId;
  final int? currentUserId;
  final void Function(CharacterEditorData saved) onSaved;
  final VoidCallback? onUnauthorized;

  /// 「结果未确认」时的**只读核对**入口。创建模式没有 id 可查 ⇒ 由壳刷新真实列表
  /// 并回传是否成功；**不 pop、不改草稿**（R5：核对失败也要能继续编辑原文）。
  final Future<bool> Function()? onVerify;

  /// 统一的弹出方式：键盘避让 + **禁止遮罩/拖拽静默关闭**（dirty 必须走明确选择）。
  static Future<CharacterEditorData?> show(
    BuildContext context, {
    required AmbApiClient client,
    required CharacterEditorStrings strings,
    required int authGen,
    required int routeGen,
    required StaleCheck isStale,
    required void Function(CharacterEditorData saved) onSaved,
    int? characterId,
    int? currentUserId,
    VoidCallback? onUnauthorized,
    Future<bool> Function()? onVerify,
  }) {
    return showModalBottomSheet<CharacterEditorData>(
      context: context,
      isScrollControlled: true,
      isDismissible: false,
      enableDrag: false,
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
        child: CharacterEditorSheet(
          client: client,
          strings: strings,
          authGen: authGen,
          routeGen: routeGen,
          isStale: isStale,
          onSaved: onSaved,
          characterId: characterId,
          currentUserId: currentUserId,
          onUnauthorized: onUnauthorized,
          onVerify: onVerify,
        ),
      ),
    );
  }

  @override
  State<CharacterEditorSheet> createState() => _CharacterEditorSheetState();
}

class _CharacterEditorSheetState extends State<CharacterEditorSheet> {
  late final CharacterEditorController _c;
  final _name = TextEditingController();
  final _bio = TextEditingController();
  final _personality = TextEditingController();
  final _chatStyle = TextEditingController();
  final _greeting = TextEditingController();
  final _gender = TextEditingController();
  final _appearance = TextEditingController();
  final _talk = TextEditingController();
  final _tz = TextEditingController();
  bool _locked = false;
  bool _verifying = false; // 只读核对进行中（防重入）
  String? _verifyOutcome; // verifyCreateRefreshed / verifyFailed

  /// **每个数字字段独立**的非法状态。不能共用一个标志：先 talk 输错、再 tz 输对
  /// 会把错误清掉，导致非法输入被静默按旧值/空值提交。
  final Map<String, bool> _invalid = <String, bool>{
    'talkativeness': false,
    'timezone_offset': false,
  };
  bool get _hasInvalidNumeric => _invalid.values.any((v) => v);

  CharacterEditorStrings get s => widget.strings;

  @override
  void initState() {
    super.initState();
    _c = CharacterEditorController(
      api: CharacterEditorApi(widget.client),
      authGen: widget.authGen,
      routeGen: widget.routeGen,
      isStale: widget.isStale,
      characterId: widget.characterId,
      currentUserId: widget.currentUserId,
    )..addListener(_onChanged);
    _c.load().then((_) => _hydrate());
  }

  void _onChanged() {
    if (mounted) setState(() {});
  }

  /// 用 GET 结果（或空草稿）填充输入框。只在 load 成功后执行一次。
  void _hydrate() {
    final d = _c.draft;
    if (d == null || !mounted) return;
    _name.text = (d['name'] as String?) ?? '';
    _bio.text = (d['bio'] as String?) ?? '';
    _personality.text = (d['personality'] as String?) ?? '';
    _chatStyle.text = (d['chat_style'] as String?) ?? '';
    _greeting.text = (d['greeting_message'] as String?) ?? '';
    _gender.text = (d['gender'] as String?) ?? '';
    _appearance.text = (d['appearance'] as String?) ?? '';
    _talk.text = d['talkativeness']?.toString() ?? '';
    _tz.text = d['timezone_offset']?.toString() ?? '';
    _locked = (d['talkativeness_locked'] as bool?) ?? false;
    setState(() {});
  }

  @override
  void dispose() {
    _c.dispose();
    for (final c in <TextEditingController>[
      _name,
      _bio,
      _personality,
      _chatStyle,
      _greeting,
      _gender,
      _appearance,
      _talk,
      _tz,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  void _syncText(String key, String value) {
    _c.draft?.setField(key, value);
    // 输入必须**立即**反映到 dirty / PopScope / 按钮可用性；
    // 否则系统/浏览器 Back 会绕过确认直接丢草稿。
    setState(() {});
  }

  void _syncInt(String key, String text) {
    final t = text.trim();
    if (t.isEmpty) {
      _invalid[key] = false;
      _c.draft?.setField(key, null); // 主动清空 ⇒ null
    } else {
      final v = int.tryParse(t);
      if (v == null) {
        _invalid[key] = true; // 只标本字段
        _c.draft?.setField(key, null); // 非法输入也算 touched ⇒ dirty
      } else {
        _invalid[key] = false;
        _c.draft?.setField(key, v);
      }
    }
    setState(() {});
  }

  String _errorText() {
    if (_hasInvalidNumeric) return s.t('invalidValue');
    final key = _c.errorKey;
    if (key == null) return '';
    switch (key) {
      case 'name_required':
        return s.t('nameRequired');
      case 'name_too_long':
        return s.t('nameTooLong');
      case 'talkativeness_out_of_range':
        return s.t('talkativenessOutOfRange');
      case 'timezone_out_of_range':
        return s.t('timezoneOutOfRange');
      case 'talkativeness_invalid':
      case 'timezone_invalid':
      case 'talkativeness_locked_invalid':
        return s.t('invalidValue');
      default:
        return s.t(key);
    }
  }

  Future<bool> _confirmDiscard() async {
    final keep = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(s.t('discardTitle')),
        content: Text(s.t('discardBody')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(s.t('keepEditing'))),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(s.t('discard'))),
        ],
      ),
    );
    return keep ?? true; // 默认继续编辑（不静默丢）
  }

  Future<void> _close() async {
    if (_c.saving || _verifying) return;
    if (_c.isDirty) {
      final keep = await _confirmDiscard();
      if (keep) return;
    }
    if (mounted) Navigator.pop(context);
  }

  Future<void> _save() async {
    if (_verifying) return;
    if (_hasInvalidNumeric) {
      setState(() {});
      return;
    }
    final saved = await _c.save();
    if (!mounted || saved == null) return;
    widget.onSaved(saved);
    Navigator.pop(context, saved);
  }

  /// 只读核对：编辑走 GET 原 id；创建无 id ⇒ 交给壳刷新真实列表（不按同名猜成功）。
  ///
  /// R5：**绝不先 pop**。失败时表单与原文必须还在；核对中防重入；晚到结果只更新本表单。
  Future<void> _verify() async {
    if (_verifying || _c.saving) return;
    final cb = widget.onVerify;
    if (!_c.isEdit && cb == null) return;
    setState(() {
      _verifying = true;
      _verifyOutcome = null;
    });
    try {
      if (_c.isEdit) {
        await _c.verifyByGet();
      } else {
        var ok = false;
        try {
          ok = await cb!();
        } catch (_) {
          ok = false;
        }
        if (!mounted || widget.isStale(widget.authGen, widget.routeGen)) return;
        setState(() =>
            _verifyOutcome = ok ? 'verifyCreateRefreshed' : 'verifyFailed');
      }
    } finally {
      if (mounted) setState(() => _verifying = false);
    }
  }

  Widget _field(
    TextEditingController controller,
    String labelKey, {
    String? hintKey,
    int maxLines = 1,
    bool numeric = false,
    ValueChanged<String>? onChanged,
  }) {
    final editable = !_c.saving && !_c.loading && !_verifying;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: TextField(
        controller: controller,
        enabled: editable,
        maxLines: maxLines,
        keyboardType: numeric ? TextInputType.number : TextInputType.text,
        decoration: InputDecoration(
          labelText: s.t(labelKey),
          helperText: hintKey == null ? null : s.t(hintKey),
          border: const OutlineInputBorder(),
        ),
        onChanged: onChanged,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final saving = _c.saving;
    final loading = _c.loading;
    final error = _errorText();
    final editable = !saving && !loading && !_verifying;
    final title = s.t(_c.isEdit ? 'editTitle' : 'createTitle');

    return PopScope(
      canPop: !saving && !_verifying && !_c.isDirty,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop || saving || _verifying) return;
        final keep = await _confirmDiscard();
        if (!keep && context.mounted) Navigator.pop(context);
      },
      child: SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.of(context).size.height * 0.92,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                child: Row(children: [
                  Expanded(
                    child: Text(title,
                        style: Theme.of(context).textTheme.titleMedium),
                  ),
                  IconButton(
                    tooltip: s.t('cancel'),
                    onPressed: saving || _verifying ? null : _close,
                    icon: const Icon(Icons.close),
                  ),
                ]),
              ),
              if (loading)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: CircularProgressIndicator(),
                )
              else if (_c.errorKey == 'loadFailed' && _c.draft == null)
                Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(children: [
                    Text(error),
                    const SizedBox(height: 8),
                    OutlinedButton(
                        onPressed: () => _c.load().then((_) => _hydrate()),
                        child: Text(s.t('retry'))),
                  ]),
                )
              else
                Flexible(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(s.t('basicGroup'),
                            style: Theme.of(context).textTheme.labelLarge),
                        _field(_name, 'name',
                            hintKey: 'nameHint',
                            onChanged: (v) => _syncText('name', v)),
                        _field(_bio, 'bio',
                            maxLines: 3, onChanged: (v) => _syncText('bio', v)),
                        _field(_personality, 'personality',
                            maxLines: 3,
                            onChanged: (v) => _syncText('personality', v)),
                        _field(_chatStyle, 'chatStyle',
                            onChanged: (v) => _syncText('chat_style', v)),
                        _field(_greeting, 'greetingMessage',
                            hintKey: 'greetingHint',
                            maxLines: 3,
                            onChanged: (v) => _syncText('greeting_message', v)),
                        const SizedBox(height: 12),
                        Text(s.t('moreGroup'),
                            style: Theme.of(context).textTheme.labelLarge),
                        _field(_gender, 'gender',
                            onChanged: (v) => _syncText('gender', v)),
                        _field(_appearance, 'appearance',
                            maxLines: 3,
                            onChanged: (v) => _syncText('appearance', v)),
                        _field(_talk, 'talkativeness',
                            hintKey: 'talkativenessHint',
                            numeric: true,
                            onChanged: (v) => _syncInt('talkativeness', v)),
                        SwitchListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(s.t('talkativenessLocked')),
                          value: _locked,
                          onChanged: editable
                              ? (v) {
                                  setState(() => _locked = v);
                                  _c.draft?.setField('talkativeness_locked', v);
                                }
                              : null,
                        ),
                        _field(_tz, 'timezoneOffset',
                            hintKey: 'timezoneHint',
                            numeric: true,
                            onChanged: (v) => _syncInt('timezone_offset', v)),
                        if (error.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.only(top: 4, bottom: 8),
                            child: Text(error,
                                style: TextStyle(
                                    color:
                                        Theme.of(context).colorScheme.error)),
                          ),
                        // 核对解除未确认后，结果仍须独立显示。
                        if (_c.unconfirmed ||
                            _c.verifyResult != null ||
                            _verifyOutcome != null)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                if (_c.unconfirmed)
                                  Text(s.t('resultUnconfirmed')),
                                const SizedBox(height: 6),
                                if (_c.unconfirmed)
                                  OutlinedButton(
                                    onPressed: _verifying ? null : _verify,
                                    child: Text(_verifying
                                        ? s.t('verifying')
                                        : s.t('verifyNow')),
                                  ),
                                if (_verifyOutcome != null)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 4),
                                    child: Text(s.t(_verifyOutcome!)),
                                  ),
                                if (_c.verifyResult != null)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 4),
                                    child: Text(s.t(_c.verifyResult!)),
                                  ),
                              ],
                            ),
                          ),
                        const SizedBox(height: 8),
                        Row(children: [
                          Expanded(
                            child: OutlinedButton(
                              onPressed: saving || _verifying ? null : _close,
                              child: Text(s.t('cancel')),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: FilledButton(
                              onPressed: (!saving &&
                                      !_verifying &&
                                      _c.canSubmit &&
                                      !_hasInvalidNumeric)
                                  ? _save
                                  : null,
                              child: Text(saving
                                  ? s.t('saving')
                                  : s.t(_c.isEdit ? 'save' : 'create')),
                            ),
                          ),
                        ]),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
