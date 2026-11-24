// PWA「角色设置」整页（2026-11-24）：把 App 角色详情页 → 设置的六组搬到 PWA。
//
// 数据：`GET /api/v1/scheduler/settings/{id}`（ProactiveSettings 全字段）+
//       角色级 `cognitive_loop_enabled`（GET /characters/{id} 读、PUT /characters/{id} 写）。
// 写入：**一次只写一个字段**（PUT /scheduler/settings/{id}）；失败回滚 UI 并提示，
//       不做整页回写、不吞错。父子级联规则与 App `character_settings_screen.dart` 对齐：
//       线上生活→分享/强度、打卡→控制（未开放）、生图→主动生图、主动消息→记忆复习/
//       频率/免打扰、朋友圈→评论、隐私→隐私锁/显示工具/思考挡位、状态→冷战（心情标识独立）。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'character_world_page.dart';
import 'memory_trace_page.dart';

class CharacterSettingsPage extends StatefulWidget {
  const CharacterSettingsPage({
    super.key,
    required this.client,
    required this.editor,
    required this.characterId,
    required this.characterName,
    required this.rs,
  });

  final AmbApiClient client;
  final EditorApi editor;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;

  @override
  State<CharacterSettingsPage> createState() => _CharacterSettingsPageState();
}

class _CharacterSettingsPageState extends State<CharacterSettingsPage> {
  late final ReaderApi _reader = ReaderApi(widget.client);
  late final CharacterSettingsApi _writer = CharacterSettingsApi(widget.client);

  Map<String, dynamic>? _settings;
  bool _cognitiveLoop = true;
  bool _loading = true;
  String _error = '';
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final results = await Future.wait([
        _reader.getProactiveSettings(widget.characterId),
        _reader.getCharacter(widget.characterId),
      ]);
      if (!mounted) return;
      setState(() {
        _settings = (results[0] as Map).cast<String, dynamic>();
        _cognitiveLoop = (results[1] as ReaderCharacter).cognitiveLoopEnabled;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  bool _flag(String key, {bool fallback = false}) =>
      (_settings?[key] as bool?) ?? fallback;

  String _str(String key, String fallback) =>
      (_settings?[key] as String?) ?? fallback;

  int _int(String key, int fallback) {
    final v = _settings?[key];
    return v is num ? v.toInt() : fallback;
  }

  /// 单字段写回：乐观更新 + 失败回滚（与 App 设置页同口径）。
  Future<void> _setField(String field, Object value) async {
    final before = _settings?[field];
    setState(() {
      _settings = {...?_settings, field: value};
      _saving = true;
    });
    try {
      await _writer.updateProactive(widget.characterId, {field: value});
      if (!mounted) return;
      setState(() => _saving = false);
      _toast(widget.rs.t('csSaved'));
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _settings = {...?_settings, field: before};
        _saving = false;
      });
      _toast(widget.rs.t('csSaveFailed'));
    }
  }

  Future<void> _setCognitiveLoop(bool value) async {
    final before = _cognitiveLoop;
    setState(() {
      _cognitiveLoop = value;
      _saving = true;
    });
    try {
      await _writer.setCognitiveLoop(widget.characterId, enabled: value);
      if (!mounted) return;
      setState(() => _saving = false);
      _toast(widget.rs.t('csSaved'));
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _cognitiveLoop = before;
        _saving = false;
      });
      _toast(widget.rs.t('csSaveFailed'));
    }
  }

  void _toast(String text) {
    final messenger = ScaffoldMessenger.of(context);
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
          content: Text(text), duration: const Duration(milliseconds: 1200)));
  }

  Future<void> _pickTime(String field, String current) async {
    int h = 0, m = 0;
    final parts = current.split(':');
    if (parts.length == 2) {
      h = int.tryParse(parts[0]) ?? 0;
      m = int.tryParse(parts[1]) ?? 0;
    }
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: h, minute: m),
    );
    if (picked == null) return;
    final text = '${picked.hour.toString().padLeft(2, '0')}:'
        '${picked.minute.toString().padLeft(2, '0')}';
    await _setField(field, text);
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final s = _settings;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('${rs.t('csTitle')} · ${widget.characterName}'),
            if (_saving)
              Text(rs.t('save'),
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: theme.colorScheme.primary)),
          ],
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(_error,
                          textAlign: TextAlign.center,
                          style: TextStyle(color: theme.colorScheme.error)),
                    ),
                    OutlinedButton(onPressed: _load, child: Text(rs.t('retry'))),
                  ]),
                )
              : ListView(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 32),
                  children: [
                    ..._dailyGroup(),
                    ..._creationGroup(),
                    ..._worldGroup(),
                    ..._socialGroup(),
                    ..._privacyGroup(),
                    ..._statusGroup(),
                    ..._debugGroup(),
                    if (s == null) Text(rs.t('pageLoadFailed')),
                  ],
                ),
    );
  }

  // ── 组容器 ────────────────────────────────────────────────────
  List<Widget> _group(String title, List<Widget> children) => [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 10, 4, 6),
          child: Text(title,
              style: Theme.of(context).textTheme.titleSmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant)),
        ),
        Card(
          margin: EdgeInsets.zero,
          child: Column(children: children),
        ),
      ];

  Widget _divider() => const Divider(height: 1, indent: 16);

  Widget _switchTile({
    required IconData icon,
    required String title,
    required String hint,
    required bool value,
    required ValueChanged<bool>? onChanged,
    bool child = false,
  }) =>
      SwitchListTile(
        dense: true,
        secondary: Icon(icon, size: 20),
        contentPadding: EdgeInsets.only(left: child ? 28 : 16, right: 12),
        title: Text(title, style: const TextStyle(fontSize: 14)),
        subtitle: Text(hint, style: const TextStyle(fontSize: 11)),
        value: value,
        onChanged: onChanged,
      );

  Widget _segTile<T>({
    required String title,
    required String hint,
    required T value,
    required List<(T, String)> options,
    required ValueChanged<T>? onChanged,
  }) =>
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 16, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontSize: 14)),
            const SizedBox(height: 2),
            Text(hint, style: const TextStyle(fontSize: 11)),
            const SizedBox(height: 8),
            SegmentedButton<T>(
              segments: [
                for (final (v, label) in options)
                  ButtonSegment(value: v, label: Text(label)),
              ],
              selected: {value},
              onSelectionChanged:
                  onChanged == null ? null : (sel) => onChanged(sel.first),
              showSelectedIcon: false,
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
            ),
          ],
        ),
      );

  Widget _timeRow(String label, String field, String value, bool enabled) => ListTile(
        dense: true,
        contentPadding: const EdgeInsets.only(left: 28, right: 12),
        title: Text(label, style: const TextStyle(fontSize: 13)),
        trailing: TextButton(
          onPressed: enabled ? () => _pickTime(field, value) : null,
          child: Text(value),
        ),
      );

  Widget _navTile(String title, String hint, VoidCallback onTap) => ListTile(
        dense: true,
        leading: const Icon(Icons.chevron_right, size: 18),
        title: Text(title, style: const TextStyle(fontSize: 14)),
        subtitle: Text(hint, style: const TextStyle(fontSize: 11)),
        onTap: onTap,
      );

  // ── 日常 ──────────────────────────────────────────────────────
  List<Widget> _dailyGroup() {
    final rs = widget.rs;
    final life = _flag('life_enabled', fallback: true);
    return [
      ..._group(rs.t('csGroupDaily'), [
        _switchTile(
          icon: Icons.book_outlined,
          title: rs.t('csDiary'),
          hint: rs.t('csDiaryHint'),
          value: _flag('diary_enabled'),
          onChanged: (v) => _setField('diary_enabled', v),
        ),
        _divider(),
        _switchTile(
          icon: Icons.self_improvement_outlined,
          title: rs.t('csOfflineLife'),
          hint: rs.t('csOfflineLifeHint'),
          value: life,
          onChanged: (v) => _setField('life_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.share_outlined,
          title: rs.t('csLifeShare'),
          hint: rs.t('csLifeShareHint'),
          value: _flag('life_share_enabled', fallback: true) && life,
          onChanged: life ? (v) => _setField('life_share_enabled', v) : null,
        ),
        _segTile<String>(
          title: rs.t('csLifeIntensity'),
          hint: rs.t('csLifeIntensityHint'),
          value: _str('life_intensity', 'low'),
          options: [
            ('low', rs.t('csFreqLow')),
            ('medium', rs.t('csFreqMedium')),
            ('high', rs.t('csFreqHigh')),
          ],
          onChanged: life ? (v) => _setField('life_intensity', v) : null,
        ),
        _divider(),
        _switchTile(
          icon: Icons.visibility_outlined,
          title: rs.t('csCheckIn'),
          hint: rs.t('csCheckInHint'),
          value: _flag('check_in_enabled'),
          onChanged: (v) => _setField('check_in_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.tune,
          title: rs.t('csControl'),
          hint: rs.t('csControlComingSoon'),
          value: false,
          onChanged: null,
        ),
      ]),
    ];
  }

  // ── 创作 ──────────────────────────────────────────────────────
  List<Widget> _creationGroup() {
    final rs = widget.rs;
    final imageGen = _flag('image_gen_enabled');
    return [
      ..._group(rs.t('csGroupCreation'), [
        _switchTile(
          icon: Icons.auto_awesome,
          title: rs.t('csImageGen'),
          hint: rs.t('csImageGenHint'),
          value: imageGen,
          onChanged: (v) => _setField('image_gen_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.brush_outlined,
          title: rs.t('csActiveImageGen'),
          hint: rs.t('csActiveImageGenHint'),
          value: _flag('active_image_gen_enabled') && imageGen,
          onChanged:
              imageGen ? (v) => _setField('active_image_gen_enabled', v) : null,
        ),
      ]),
    ];
  }

  // ── 世界（入口）───────────────────────────────────────────────
  List<Widget> _worldGroup() {
    final rs = widget.rs;
    return [
      ..._group(rs.t('csGroupWorld'), [
        _navTile(rs.t('worldSettings'), rs.t('worldSettingsHint'), () {
          Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => CharacterWorldPage(
              client: widget.client,
              editor: widget.editor,
              characterId: widget.characterId,
              characterName: widget.characterName,
              rs: widget.rs,
            ),
          ));
        }),
      ]),
    ];
  }

  // ── 社交 ──────────────────────────────────────────────────────
  List<Widget> _socialGroup() {
    final rs = widget.rs;
    final proactive = _flag('enable_proactive');
    final dnd = _flag('dnd_enabled');
    final moments = _flag('moments_enabled', fallback: true);
    return [
      ..._group(rs.t('csGroupSocial'), [
        _switchTile(
          icon: Icons.psychology_outlined,
          title: rs.t('csCognitiveLoop'),
          hint: rs.t('csCognitiveLoopHint'),
          value: _cognitiveLoop,
          onChanged: _setCognitiveLoop,
        ),
        _divider(),
        _switchTile(
          icon: Icons.all_inclusive,
          title: rs.t('csWeaveFullInject'),
          hint: rs.t('csWeaveFullInjectHint'),
          value: _flag('weave_full_inject_enabled'),
          onChanged: (v) => _setField('weave_full_inject_enabled', v),
        ),
        _divider(),
        _switchTile(
          icon: Icons.notifications_active_outlined,
          title: rs.t('csProactive'),
          hint: rs.t('csProactiveHint'),
          value: proactive,
          onChanged: (v) => _setField('enable_proactive', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.history_edu_outlined,
          title: rs.t('csMemoryReview'),
          hint: rs.t('csMemoryReviewHint'),
          value: _flag('memory_review_enabled') && proactive,
          onChanged: proactive
              ? (v) => _setField('memory_review_enabled', v)
              : null,
        ),
        _segTile<String>(
          title: rs.t('csFrequency'),
          hint: rs.t('csFrequencyHint'),
          value: _str('frequency', 'medium'),
          options: [
            ('low', rs.t('csFreqLow')),
            ('medium', rs.t('csFreqMedium')),
            ('high', rs.t('csFreqHigh')),
          ],
          onChanged: proactive ? (v) => _setField('frequency', v) : null,
        ),
        _switchTile(
          child: true,
          icon: Icons.do_not_disturb_on_outlined,
          title: rs.t('csDnd'),
          hint: dnd
              ? '${rs.t('csDndOn')} ${_str('dnd_start', '00:00')}–${_str('dnd_end', '07:00')}'
              : rs.t('csDndOff'),
          value: dnd,
          onChanged: proactive ? (v) => _setField('dnd_enabled', v) : null,
        ),
        if (dnd && proactive) ...[
          _timeRow(rs.t('csStart'), 'dnd_start', _str('dnd_start', '00:00'), true),
          _timeRow(rs.t('csEnd'), 'dnd_end', _str('dnd_end', '07:00'), true),
        ],
        _divider(),
        _switchTile(
          icon: Icons.people_outline,
          title: rs.t('csMoments'),
          hint: rs.t('csMomentsHint'),
          value: moments,
          onChanged: (v) => _setField('moments_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.mode_comment_outlined,
          title: rs.t('csMomentsComment'),
          hint: rs.t('csMomentsCommentHint'),
          value: _flag('moments_comment_enabled', fallback: true) && moments,
          onChanged: moments
              ? (v) => _setField('moments_comment_enabled', v)
              : null,
        ),
      ]),
    ];
  }

  // ── 隐私 ──────────────────────────────────────────────────────
  List<Widget> _privacyGroup() {
    final rs = widget.rs;
    final privacy = _flag('privacy_enabled', fallback: true);
    return [
      ..._group(rs.t('csGroupPrivacy'), [
        _switchTile(
          icon: Icons.lock_outline,
          title: rs.t('csPrivacy'),
          hint: rs.t('csPrivacyHint'),
          value: privacy,
          onChanged: (v) => _setField('privacy_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.enhanced_encryption_outlined,
          title: rs.t('csPrivacyLock'),
          hint: rs.t('csPrivacyLockHint'),
          value: _flag('privacy_lock_enabled', fallback: true) && privacy,
          onChanged:
              privacy ? (v) => _setField('privacy_lock_enabled', v) : null,
        ),
        _switchTile(
          child: true,
          icon: Icons.build_outlined,
          title: rs.t('csShowTools'),
          hint: rs.t('csShowToolsHint'),
          value: _flag('show_tools_enabled') && privacy,
          onChanged: privacy ? (v) => _setField('show_tools_enabled', v) : null,
        ),
        _segTile<int>(
          title: rs.t('csReasoning'),
          hint: rs.t('csReasoningHint'),
          value: _int('reasoning_level', 0),
          options: [
            (0, rs.t('csReasoningOff')),
            (1, rs.t('csReasoningSimple')),
            (2, rs.t('csReasoningDeep')),
          ],
          onChanged:
              privacy ? (v) => _setField('reasoning_level', v) : null,
        ),
      ]),
    ];
  }

  // ── 状态 ──────────────────────────────────────────────────────
  List<Widget> _statusGroup() {
    final rs = widget.rs;
    final stateTrigger = _flag('state_trigger_enabled');
    return [
      ..._group(rs.t('csGroupStatus'), [
        _switchTile(
          icon: Icons.mood_bad_outlined,
          title: rs.t('csStateTrigger'),
          hint: rs.t('csStateTriggerHint'),
          value: stateTrigger,
          onChanged: (v) => _setField('state_trigger_enabled', v),
        ),
        _switchTile(
          child: true,
          icon: Icons.ac_unit_outlined,
          title: rs.t('csColdWar'),
          hint: rs.t('csColdWarHint'),
          value: _flag('cold_war_enabled') && stateTrigger,
          onChanged:
              stateTrigger ? (v) => _setField('cold_war_enabled', v) : null,
        ),
        _switchTile(
          child: true,
          icon: Icons.emoji_emotions_outlined,
          title: rs.t('csMoodBadge'),
          hint: rs.t('csMoodBadgeHint'),
          value: _flag('mood_badge_enabled'),
          onChanged: (v) => _setField('mood_badge_enabled', v),
        ),
      ]),
    ];
  }

  // ── 调试 ──────────────────────────────────────────────────────
  List<Widget> _debugGroup() {
    final rs = widget.rs;
    return [
      ..._group(rs.t('csGroupDebug'), [
        _navTile(rs.t('csMemoryTrace'), rs.t('csMemoryTraceHint'), () {
          Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => MemoryTracePage(
              client: widget.client,
              characterId: widget.characterId,
              characterName: widget.characterName,
              rs: widget.rs,
            ),
          ));
        }),
      ]),
    ];
  }
}

