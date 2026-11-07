import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../features/character/settings_sections_daily.dart';
import '../../features/character/settings_sections_social.dart';

/// 角色设置（UI 2.0：iOS 分组卡片）：日常 / 创作 / 社交 / 隐私 / 状态
/// 深拆（F7-c-7，2026-09-01）：区段 widget 迁 features/character/settings_sections*.dart，
/// 本屏保留字段、加载/写回与中央变更分发（级联语义集中于此，与拆分前逐字节等价）。
class CharacterSettingsScreen extends StatefulWidget {
  final int characterId;
  final String characterName;

  const CharacterSettingsScreen({
    super.key,
    required this.characterId,
    required this.characterName,
  });

  @override
  State<CharacterSettingsScreen> createState() => _CharacterSettingsScreenState();
}

class _CharacterSettingsScreenState extends State<CharacterSettingsScreen> {
  final _api = ApiClient();
  bool _loading = true;
  bool _proactive = true;
  bool _memoryReview = true;
  bool _diary = true;
  bool _moments = true;
  bool _momentsComment = true;
  bool _stateTrigger = true;
  bool _coldWar = true;
  bool _moodBadge = true;
  bool _imageGen = false;
  bool _activeImageGen = false;
  bool _privacyEnabled = true;
  bool _privacyLock = true;
  int _reasoningLevel = 0;
  bool _showTools = false;
  bool _weaveFullInject = false;
  bool _lifeEnabled = true;
  String _lifeIntensity = 'low';
  bool _lifeShare = true;
  String _frequency = 'medium';
  bool _dndEnabled = false;
  String _dndStart = '00:00';
  String _dndEnd = '07:00';
  bool _checkIn = false;
  bool _cognitiveLoop = false;
  final Map<String, bool> _expanded = {}; // 可展开项箭头状态（key=标题）

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getSchedulerSettings(widget.characterId);
      if (mounted) {
        setState(() {
          _proactive = data['enable_proactive'] as bool? ?? true;
          _memoryReview = data['memory_review_enabled'] as bool? ?? true;
          _diary = data['diary_enabled'] as bool? ?? true;
          _moments = data['moments_enabled'] as bool? ?? true;
          _momentsComment = data['moments_comment_enabled'] as bool? ?? true;
          _stateTrigger = data['state_trigger_enabled'] as bool? ?? true;
          _coldWar = data['cold_war_enabled'] as bool? ?? true;
          _moodBadge = data['mood_badge_enabled'] as bool? ?? true;
          _imageGen = data['image_gen_enabled'] as bool? ?? false;
          _activeImageGen = data['active_image_gen_enabled'] as bool? ?? false;
          _privacyEnabled = data['privacy_enabled'] as bool? ?? true;
          _privacyLock = data['privacy_lock_enabled'] as bool? ?? true;
          _reasoningLevel = data['reasoning_level'] as int? ?? 0;
          _showTools = data['show_tools_enabled'] as bool? ?? false;
          _weaveFullInject = data['weave_full_inject_enabled'] as bool? ?? false;
          _lifeEnabled = data['life_enabled'] as bool? ?? true;
          _lifeIntensity = data['life_intensity'] as String? ?? 'low';
          _lifeShare = data['life_share_enabled'] as bool? ?? true;
          _frequency = data['frequency'] as String? ?? 'medium';
          _dndEnabled = data['dnd_enabled'] as bool? ?? false;
          _dndStart = data['dnd_start'] as String? ?? '00:00';
          _dndEnd = data['dnd_end'] as String? ?? '07:00';
          _checkIn = data['check_in_enabled'] as bool? ?? false;
          _loading = false;
        });
      }
    } catch (e) {
      debugPrint("Settings load error: ");
      if (mounted) setState(() => _loading = false);
    }
    await _loadCognitiveLoop();
  }

  /// 读取认知循环开关（角色级字段经角色详情接口）
  Future<void> _loadCognitiveLoop() async {
    try {
      final char = await _api.getCharacter(widget.characterId);
      if (mounted) setState(() => _cognitiveLoop = char.cognitiveLoopEnabled);
    } catch (e) {
      debugPrint("Cognitive loop load error: ");
    }
  }

  /// 状态总开关联动：父开关关闭时冷战断联一同关闭（后端同字段联动）
  void _onStateChanged(bool v) {
    setState(() {
      _stateTrigger = v;
      if (!v) _coldWar = false;
    });
    _update('state_trigger_enabled', v);
    if (!v) _update('cold_war_enabled', false);
  }

  Future<void> _update(String field, dynamic value) async {
    try {
      await _api.updateSchedulerSettings(widget.characterId, {field: value});
    } catch (e) {
      debugPrint("Settings update error: ");
    }
  }

  /// 认知循环开关：角色级字段经角色更新接口写回
  void _onCognitiveLoopChanged(bool v) {
    setState(() => _cognitiveLoop = v);
    _updateCharacterField('cognitive_loop_enabled', v);
  }

  Future<void> _updateCharacterField(String field, dynamic value) async {
    try {
      await _api.updateCharacter(widget.characterId, {field: value});
    } catch (e) {
      debugPrint("Character update error: ");
    }
  }

  /// 中央变更分发（深拆后 section 统一回调）：本地状态 + 级联 + 写回，与拆分前各内联闭包逐字节等价
  void _onFieldChanged(String field, dynamic value) {
    setState(() {
      switch (field) {
        case 'diary_enabled':
          _diary = value as bool;
        case 'life_enabled':
          _lifeEnabled = value as bool;
        case 'life_share_enabled':
          _lifeShare = value as bool;
        case 'life_intensity':
          _lifeIntensity = value as String;
        case 'check_in_enabled':
          _checkIn = value as bool;
        case 'image_gen_enabled':
          _imageGen = value as bool;
          if (!value) _activeImageGen = false;
        case 'active_image_gen_enabled':
          _activeImageGen = value as bool;
        case 'weave_full_inject_enabled':
          _weaveFullInject = value as bool;
        case 'enable_proactive':
          _proactive = value as bool;
        case 'memory_review_enabled':
          _memoryReview = value as bool;
        case 'frequency':
          _frequency = value as String;
        case 'dnd_enabled':
          _dndEnabled = value as bool;
        case 'dnd_start':
          _dndStart = value as String;
        case 'dnd_end':
          _dndEnd = value as String;
        case 'moments_enabled':
          _moments = value as bool;
          if (!value) _momentsComment = false;
        case 'moments_comment_enabled':
          _momentsComment = value as bool;
        case 'privacy_enabled':
          _privacyEnabled = value as bool;
          if (!value) {
            _privacyLock = false;
            _reasoningLevel = 0;
            _showTools = false;
          }
        case 'privacy_lock_enabled':
          _privacyLock = value as bool;
        case 'reasoning_level':
          _reasoningLevel = value as int;
        case 'show_tools_enabled':
          _showTools = value as bool;
        case 'cold_war_enabled':
          _coldWar = value as bool;
        case 'mood_badge_enabled':
          _moodBadge = value as bool;
      }
    });
    _update(field, value);
    // 级联写回（与拆分前内联实现一致：父开关关闭时连带关闭子开关）
    if (field == 'image_gen_enabled' && !value) _update('active_image_gen_enabled', false);
    if (field == 'moments_enabled' && !value) _update('moments_comment_enabled', false);
    if (field == 'privacy_enabled' && !value) {
      _update('privacy_lock_enabled', false);
      _update('reasoning_level', 0);
      _update('show_tools_enabled', false);
    }
  }

  void _onExpansionToggle(String title, bool expanded) {
    setState(() => _expanded[title] = expanded);
  }

  /// 时间选择（HH:mm）
  Future<void> _pickTime(String current, String field) async {
    final parts = current.split(':');
    final t = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(
        hour: int.tryParse(parts[0]) ?? 0,
        minute: int.tryParse(parts[1]) ?? 0,
      ),
    );
    if (t == null || !mounted) return;
    final v = '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
    setState(() {
      if (field == 'dnd_start') {
        _dndStart = v;
      } else {
        _dndEnd = v;
      }
    });
    _update(field, v);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P5 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
        backgroundColor: isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55),
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          bottom: BorderSide(
            color: isDark
                ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                : Colors.black.withValues(alpha: AppGlass.borderAlpha),
            width: 0.5,
          ),
        ),
        title: Text(l10n.charSettings),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 24),
              children: [
                DailySection(
                  diary: _diary,
                  lifeEnabled: _lifeEnabled,
                  lifeShare: _lifeShare,
                  lifeIntensity: _lifeIntensity,
                  checkIn: _checkIn,
                  onFieldChanged: _onFieldChanged,
                  expanded: _expanded,
                  onExpansionToggle: _onExpansionToggle,
                ),
                CreationSection(
                  imageGen: _imageGen,
                  activeImageGen: _activeImageGen,
                  onFieldChanged: _onFieldChanged,
                  expanded: _expanded,
                  onExpansionToggle: _onExpansionToggle,
                ),
                WorldSection(characterId: widget.characterId),
                SocialSection(
                  cognitiveLoop: _cognitiveLoop,
                  onCognitiveLoopChanged: _onCognitiveLoopChanged,
                  weaveFullInject: _weaveFullInject,
                  proactive: _proactive,
                  moments: _moments,
                  momentsComment: _momentsComment,
                  memoryReview: _memoryReview,
                  frequency: _frequency,
                  dndEnabled: _dndEnabled,
                  dndStart: _dndStart,
                  dndEnd: _dndEnd,
                  onFieldChanged: _onFieldChanged,
                  expanded: _expanded,
                  onExpansionToggle: _onExpansionToggle,
                  onPickTime: _pickTime,
                ),
                PrivacySection(
                  privacyEnabled: _privacyEnabled,
                  privacyLock: _privacyLock,
                  showTools: _showTools,
                  reasoningLevel: _reasoningLevel,
                  onFieldChanged: _onFieldChanged,
                  expanded: _expanded,
                  onExpansionToggle: _onExpansionToggle,
                ),
                StatusSection(
                  stateTrigger: _stateTrigger,
                  onStateChanged: _onStateChanged,
                  coldWar: _coldWar,
                  moodBadge: _moodBadge,
                  onFieldChanged: _onFieldChanged,
                  expanded: _expanded,
                  onExpansionToggle: _onExpansionToggle,
                ),
                TraceSection(
                  characterId: widget.characterId,
                  characterName: widget.characterName,
                ),
              ],
            ),
    );
  }
}
