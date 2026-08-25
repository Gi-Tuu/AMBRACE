import 'dart:io';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import "package:ai_companion/theme/tokens.dart";

// 所在时区预设（label 展示 / offset 为 UTC 偏移小时；null=默认北京时间）
const List<({String key, String label})> kTimezoneOptions = [
  (key: 'default', label: '默认（北京时间 UTC+8）'),
  (key: '8', label: '北京 UTC+8'),
  (key: '9', label: '东京 UTC+9'),
  (key: '4', label: '迪拜 UTC+4'),
  (key: '3', label: '莫斯科 UTC+3'),
  (key: '1', label: '巴黎 UTC+1'),
  (key: '0', label: '伦敦 UTC+0'),
  (key: '-5', label: '纽约 UTC-5'),
  (key: '-8', label: '洛杉矶 UTC-8'),
  (key: '10', label: '悉尼 UTC+10'),
];

// 音色预设（与后端 tts_service.VOICE_PRESETS 保持一致）
const List<({String key, String label})> kVoicePresets = [
  (key: 'xiaoxiao', label: '晓晓 · 自然女声'),
  (key: 'xiaoyi', label: '晓伊 · 年轻女声'),
  (key: 'xiaobei', label: '晓北 · 东北女声'),
  (key: 'xiaoni', label: '晓妮 · 陕西女声'),
  (key: 'hiugaai', label: '曉佳 · 粤语女声'),
  (key: 'hiumaan', label: '曉曼 · 粤语女声'),
  (key: 'hsiaochen', label: '曉臻 · 台湾女声'),
  (key: 'yunxi', label: '云希 · 青年男声'),
  (key: 'yunjian', label: '云健 · 磁性男声'),
  (key: 'yunyang', label: '云扬 · 新闻男声'),
  (key: 'yunfeng', label: '云枫 · 成熟男声'),
  (key: 'wanlung', label: '雲龍 · 粤语男声'),
];

class CharacterEditScreen extends StatefulWidget {
  final AICharacter? character;
  const CharacterEditScreen({super.key, this.character});

  @override
  State<CharacterEditScreen> createState() => _CharacterEditScreenState();
}

class _CharacterEditScreenState extends State<CharacterEditScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _nameCtrl;
  late final TextEditingController _personalityCtrl;
  late final TextEditingController _styleCtrl;
  late final TextEditingController _appearanceCtrl;
  late final TextEditingController _heightCtrl;
  late final TextEditingController _weightCtrl;
  late final TextEditingController _birthdayCtrl;
  late final TextEditingController _bioCtrl;
  String _gender = '';
  String _voice = '';
  double _voiceRate = 1.0;
  double _voicePitch = 0.0;
  int? _timezoneOffset;
  String? _avatarUrl;
  bool _uploadingAvatar = false;
  bool _saving = false;
  // 话痨度（群聊调度 L1，2026-08-25）：0-100；未触碰时提交 null（后端按性格推断）
  double _talkativeness = 50;
  bool _talkativenessLocked = false;
  bool _talkativenessSet = false;
  final AudioPlayer _previewPlayer = AudioPlayer();
  bool _previewing = false;
  bool get isEditing => widget.character != null;

  @override
  void initState() {
    super.initState();
    final c = widget.character;
    _nameCtrl = TextEditingController(text: c?.name ?? '');
    _personalityCtrl = TextEditingController(text: c?.personality ?? '');
    _styleCtrl = TextEditingController(text: c?.chatStyle ?? '');
    _appearanceCtrl = TextEditingController(text: c?.appearance ?? '');
    _heightCtrl = TextEditingController(text: c?.height?.toString() ?? '');
    _weightCtrl = TextEditingController(text: c?.weight?.toString() ?? '');
    _birthdayCtrl = TextEditingController(text: c?.birthday ?? '');
    _bioCtrl = TextEditingController(text: c?.bio ?? '');
    _gender = c?.gender ?? '';
    _voice = c?.voice ?? '';
    _voiceRate = c?.voiceRate ?? 1.0;
    _voicePitch = c?.voicePitch ?? 0.0;
    _timezoneOffset = c?.timezoneOffset;
    _avatarUrl = c?.avatarUrl;
    _talkativeness = (c?.talkativeness ?? 50).toDouble();
    _talkativenessLocked = c?.talkativenessLocked ?? false;
    _talkativenessSet = c?.talkativeness != null;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _personalityCtrl.dispose();
    _styleCtrl.dispose();
    _appearanceCtrl.dispose();
    _heightCtrl.dispose();
    _weightCtrl.dispose();
    _birthdayCtrl.dispose();
    _bioCtrl.dispose();
    _previewPlayer.dispose();
    super.dispose();
  }

  /// 试听当前音色：固定文案合成并播放（音色/语速/语调即时生效）
  Future<void> _previewVoice() async {
    final l10n = AppLocalizations.of(context)!;
    if (_previewing) return;
    setState(() => _previewing = true);
    try {
      final url = await ApiClient().speechPreview(
        voice: _voice,
        voiceRate: _voiceRate,
        voicePitch: _voicePitch,
        gender: _gender,
      );
      if (!mounted) return;
      if (url.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.voicePreviewFailConfig)),
        );
        return;
      }
      await _previewPlayer.stop();
      await _previewPlayer.play(UrlSource(ApiClient().resolveUrl(url)));
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.voicePreviewFailNet)),
        );
      }
    } finally {
      if (mounted) setState(() => _previewing = false);
    }
  }

  Future<void> _pickAvatar() async {
    final l10n = AppLocalizations.of(context)!;
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1024,
      maxHeight: 1024,
      imageQuality: 85,
    );
    if (picked == null || !mounted) return;
    setState(() => _uploadingAvatar = true);
    try {
      final up = await ApiClient().uploadAvatar(File(picked.path));
      final url = up["url"] as String? ?? "";
      if (url.isEmpty) throw Exception("empty url");
      if (mounted) setState(() => _avatarUrl = url);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.avatarUploadFail)));
      }
    } finally {
      if (mounted) setState(() => _uploadingAvatar = false);
    }
  }

  Widget _buildVoiceRateSlider() {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, top: 12, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.voiceRate, style: const TextStyle(fontSize: 15)),
              Text('${_voiceRate.toStringAsFixed(1)}x',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          Slider(
            value: _voiceRate.clamp(0.5, 1.5).toDouble(),
            min: 0.5,
            max: 1.5,
            divisions: 10,
            label: '${_voiceRate.toStringAsFixed(1)}x',
            onChanged: (v) => setState(() => _voiceRate = v),
          ),
        ],
      ),
    );
  }

  Widget _buildVoicePitchSlider() {
    final l10n = AppLocalizations.of(context)!;
    final hz = _voicePitch.toStringAsFixed(0);
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, top: 8, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.voicePitch, style: const TextStyle(fontSize: 15)),
              Text(_voicePitch == 0 ? l10n.pitchNormal : '${_voicePitch > 0 ? '+' : ''}$hz Hz',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          Slider(
            value: _voicePitch.clamp(-20, 20).toDouble(),
            min: -20,
            max: 20,
            divisions: 8,
            label: '$hz Hz',
            onChanged: (v) => setState(() => _voicePitch = v),
          ),
        ],
      ),
    );
  }

  Widget _buildField(
    TextEditingController ctrl, {
    required String label,
    String? hint,
    int maxLines = 1,
    TextInputType? keyboard,
    String? Function(String?)? validator,
    bool compact = false,
  }) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: EdgeInsets.only(
          left: compact ? 8 : 16, right: compact ? 8 : 16,
          top: 10, bottom: 10),
      child: TextFormField(
        controller: ctrl,
        validator: validator,
        keyboardType: keyboard,
        maxLines: maxLines,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        ),
      ),
    );
  }

  Widget _buildDropdown({
    required String label,
    required String? value,
    required List<DropdownMenuItem<String>> items,
    required ValueChanged<String?> onChanged,
    String? helper,
  }) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, top: 10, bottom: 10),
      child: DropdownButtonFormField<String>(
        initialValue: value,
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
          helperStyle: const TextStyle(fontSize: 11),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        ),
        items: items,
        onChanged: onChanged,
      ),
    );
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    if (!_formKey.currentState!.validate()) return;
    setState(() => _saving = true);

    final api = ApiClient();
    final data = {
      'name': _nameCtrl.text,
      'avatar_url': _avatarUrl,
      'personality': _personalityCtrl.text,
      'chat_style': _styleCtrl.text,
      'appearance': _appearanceCtrl.text,
      'height': _heightCtrl.text.isNotEmpty ? int.tryParse(_heightCtrl.text) : null,
      'weight': _weightCtrl.text.isNotEmpty ? int.tryParse(_weightCtrl.text) : null,
      'gender': _gender.isNotEmpty ? _gender : null,
      'birthday': _birthdayCtrl.text.trim().isNotEmpty ? _birthdayCtrl.text.trim() : null,
      'bio': _bioCtrl.text.trim().isNotEmpty ? _bioCtrl.text.trim() : null,
      'voice': _voice.isNotEmpty ? _voice : null,
      'voice_rate': _voiceRate,
      'voice_pitch': _voicePitch,
      'timezone_offset': _timezoneOffset,
      'talkativeness': _talkativenessSet ? _talkativeness.round() : null,
      'talkativeness_locked': _talkativenessLocked,
    };

    try {
      AICharacter? created;
      if (isEditing) {
        await api.updateCharacter(widget.character!.id, data);
      } else {
        created = await api.createCharacter(data);
      }
      if (mounted) {
        // 创建角色成功后：一次性询问「是否生成问候语？」（编辑已有角色不弹，保留字段）
        if (!isEditing && created != null) {
          await _maybeGenerateGreeting(created);
        }
        if (mounted) Navigator.pop(context, true);
      }
    } catch (e) {
      setState(() => _saving = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.saveFail)),
        );
      }
    }
  }

  /// 创建角色成功后一次性询问「是否生成问候语？」；点生成则调 LLM 写回 greeting_message。
  Future<void> _maybeGenerateGreeting(AICharacter created) async {
    final l10n = AppLocalizations.of(context)!;
    final want = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.generateGreetingAsk),
        content: Text(l10n.generateGreetingDesc),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(l10n.generateGreetingSkip),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.generateGreetingDo),
          ),
        ],
      ),
    );
    if (want != true || !mounted) return;
    try {
      await ApiClient().generateGreeting(created.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.generateGreetingDone)),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.generateGreetingFail)),
        );
      }
    }
  }

  /// 话痨度（0-100 滑块）+ 锁定开关（talkativeness_locked，锁后 AI 不可自主调整）。
  Widget _buildTalkativeness() {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, top: 12, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.talkativeness, style: const TextStyle(fontSize: 15)),
              Text('${_talkativeness.round()}',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          Slider(
            value: _talkativeness.clamp(0, 100).toDouble(),
            min: 0,
            max: 100,
            divisions: 100,
            label: '${_talkativeness.round()}',
            onChanged: (v) => setState(() {
              _talkativeness = v;
              _talkativenessSet = true;
            }),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            dense: true,
            title: Text(l10n.talkativenessLocked,
                style: const TextStyle(fontSize: 13)),
            subtitle: Text(l10n.talkativenessLockedHint,
                style: TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
            value: _talkativenessLocked,
            onChanged: (v) => setState(() => _talkativenessLocked = v),
          ),
        ],
      ),
    );
  }

  String _tzLabel(AppLocalizations l10n, String key) {
    switch (key) {
      case 'default': return l10n.tzDefault;
      case '8': return l10n.tzBeijing;
      case '9': return l10n.tzTokyo;
      case '4': return l10n.tzDubai;
      case '3': return l10n.tzMoscow;
      case '1': return l10n.tzParis;
      case '0': return l10n.tzLondon;
      case '-5': return l10n.tzNewYork;
      case '-8': return l10n.tzLosAngeles;
      case '10': return l10n.tzSydney;
      default: return key;
    }
  }

  String _voiceLabel(AppLocalizations l10n, String key) {
    switch (key) {
      case 'xiaoxiao': return l10n.voiceXiaoxiao;
      case 'xiaoyi': return l10n.voiceXiaoyi;
      case 'xiaobei': return l10n.voiceXiaobei;
      case 'xiaoni': return l10n.voiceXiaoni;
      case 'hiugaai': return l10n.voiceXiaojia;
      case 'hiumaan': return l10n.voiceXiaoman;
      case 'hsiaochen': return l10n.voiceXiaozhen;
      case 'yunxi': return l10n.voiceYunxi;
      case 'yunjian': return l10n.voiceYunjian;
      case 'yunyang': return l10n.voiceYunyang;
      case 'yunfeng': return l10n.voiceYunfeng;
      case 'wanlung': return l10n.voiceYunlong;
      default: return key;
    }
  }

  String _genderLabel(AppLocalizations l10n, String g) {
    switch (g) {
      case '男': return l10n.genderMale;
      case '女': return l10n.genderFemale;
      case '其他': return l10n.genderOther;
      default: return g;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(isEditing ? l10n.editFriend : l10n.createFriend),
        actions: [
          TextButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l10n.save),
          ),
        ],
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Center(
                child: GestureDetector(
                  onTap: _uploadingAvatar ? null : _pickAvatar,
                  child: Stack(
                    children: [
                      CircleAvatar(
                        radius: 44,
                        backgroundColor: scheme.secondaryContainer,
                        child: _avatarUrl != null && _avatarUrl!.isNotEmpty
                            ? ClipOval(
                                child: Image.network(
                                  ApiClient().resolveUrl(_avatarUrl!),
                                  width: 88,
                                  height: 88,
                                  fit: BoxFit.cover,
                                  errorBuilder: (context, error, stack) => Text(
                                    _nameCtrl.text.isNotEmpty ? _nameCtrl.text[0] : '?',
                                    style: const TextStyle(fontSize: 34),
                                  ),
                                ),
                              )
                            : Text(
                                _nameCtrl.text.isNotEmpty ? _nameCtrl.text[0] : '?',
                                style: const TextStyle(fontSize: 34),
                              ),
                      ),
                      if (_uploadingAvatar)
                        const Positioned.fill(
                          child: Center(child: CircularProgressIndicator()),
                        ),
                      Positioned(
                        bottom: 0,
                        right: 0,
                        child: Container(
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(color: scheme.primary, shape: BoxShape.circle),
                          child: Icon(Icons.photo_camera, size: 16, color: scheme.onPrimary),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Center(
              child: Text(l10n.tapToPickAvatar,
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 12)),
            ),
            const SizedBox(height: 12),
            IosCardGroup(
              title: l10n.basicInfo,
              children: [
                _buildField(_nameCtrl,
                    label: l10n.name,
                    validator: (v) => (v == null || v.isEmpty) ? l10n.nameRequired : null),
                const IosCardDivider(),
                _buildField(_appearanceCtrl,
                    label: l10n.appearance, hint: l10n.appearanceHint, maxLines: 2),
                const IosCardDivider(),
                Row(
                  children: [
                    Expanded(
                      child: _buildField(_heightCtrl,
                          label: l10n.heightCm, hint: '175', keyboard: TextInputType.number, compact: true),
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: _buildField(_weightCtrl,
                          label: l10n.weightKg, hint: '65', keyboard: TextInputType.number, compact: true),
                    ),
                  ],
                ),
                const IosCardDivider(),
                _buildField(_birthdayCtrl,
                    label: l10n.birthday, hint: l10n.birthdayHint, keyboard: TextInputType.datetime),
                const IosCardDivider(),
                _buildDropdown(
                  label: l10n.gender,
                  value: _gender.isNotEmpty ? _gender : null,
                  items: ['男', '女', '其他']
                      .map((g) => DropdownMenuItem(value: g, child: Text(_genderLabel(l10n, g))))
                      .toList(),
                  onChanged: (v) => setState(() => _gender = v ?? ''),
                ),
                const IosCardDivider(),
                _buildField(_bioCtrl,
                    label: l10n.backgroundInfo,
                    hint: l10n.backgroundInfoHint, maxLines: 4),
                const IosCardDivider(),
                _buildDropdown(
                  label: l10n.timezone,
                  value: _timezoneOffset == null ? 'default' : '$_timezoneOffset',
                  helper: l10n.timezoneHelper,
                  items: kTimezoneOptions
                      .map((t) => DropdownMenuItem(value: t.key, child: Text(_tzLabel(l10n, t.key))))
                      .toList(),
                  onChanged: (v) => setState(() {
                    _timezoneOffset = (v == null || v == 'default') ? null : int.tryParse(v);
                  }),
                ),
              ],
            ),
            IosCardGroup(
              title: l10n.personalityGroup,
              children: [
                _buildField(_personalityCtrl,
                    label: l10n.personality, hint: l10n.personalityHint, maxLines: 3),
                const IosCardDivider(),
                _buildField(_styleCtrl,
                    label: l10n.chatStyle, hint: l10n.chatStyleHint, maxLines: 3),
                const IosCardDivider(),
                _buildTalkativeness(),
              ],
            ),
            IosCardGroup(
              title: l10n.voiceGroup,
              children: [
                _buildDropdown(
                  label: l10n.voiceLabel,
                  value: _voice,
                  helper: l10n.voiceHelper,
                  items: [
                    DropdownMenuItem(value: '', child: Text(l10n.voiceDefault)),
                    ...kVoicePresets.map(
                      (v) => DropdownMenuItem(value: v.key, child: Text(_voiceLabel(l10n, v.key))),
                    ),
                  ],
                  onChanged: (v) => setState(() => _voice = v ?? ''),
                ),
                const IosCardDivider(),
                _buildVoiceRateSlider(),
                const IosCardDivider(),
                _buildVoicePitchSlider(),
                const IosCardDivider(),
                InkWell(
                  onTap: _previewing ? null : _previewVoice,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    child: Row(children: [
                      _previewing
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.play_circle_outline,
                              size: 22, color: AppColors.accent),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(_previewing ? l10n.previewing : l10n.previewVoice,
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                            const SizedBox(height: 2),
                            Text(l10n.previewHint,
                                style: TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
                    ]),
                  ),
                ),
              ],
            ),
            if (isEditing) ...[
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.only(left: 16, right: 16, bottom: 16),
                child: SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.delete_outline, color: Colors.red),
                    label: Text(l10n.deleteFriend, style: const TextStyle(color: Colors.red)),
                    style: OutlinedButton.styleFrom(
                      side: const BorderSide(color: Colors.red),
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    ),
                    onPressed: () async {
                      final confirm = await showDialog<bool>(
                        context: context,
                        builder: (ctx) => AlertDialog(
                          title: Text(l10n.confirmDelete),
                          content: Text(l10n.deleteFriendConfirm(widget.character!.name)),
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
                      if (confirm == true) {
                        final api = ApiClient();
                        try {
                          await api.deleteCharacter(widget.character!.id);
                          if (context.mounted) Navigator.pop(context, true);
                        } catch (e) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text(l10n.deleteFail)),
                            );
                          }
                        }
                      }
                    },
                  ),
                ),
              ),
            ],
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}
