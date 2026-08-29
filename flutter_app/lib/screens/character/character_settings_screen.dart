import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/ios_card_group.dart';
import 'lorebook_screen.dart';
import 'world_settings_screen.dart';
import "package:ai_companion/theme/tokens.dart";

/// 角色设置（UI 2.0：iOS 分组卡片）：日常 / 创作 / 社交 / 隐私 / 状态
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
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
                _auroraGroup(title: l10n.dailyGroup, children: [
                  _groupSwitchTile(
                    icon: Icons.book_outlined,
                    title: l10n.aiDiary,
                    subtitle: l10n.aiDiaryHint,
                    value: _diary,
                    onChanged: (v) {
                      setState(() => _diary = v);
                      _update('diary_enabled', v);
                    },
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  _expansionSwitch(
                    icon: Icons.self_improvement_outlined,
                    title: l10n.aiOfflineLife,
                    subtitle: l10n.aiOfflineLifeHint,
                    value: _lifeEnabled,
                    onChanged: (v) {
                      setState(() => _lifeEnabled = v);
                      _update('life_enabled', v);
                    },
                    children: [
                      _childSwitch(
                        title: l10n.lifeShare,
                        subtitle: l10n.lifeShareHint,
                        value: _lifeShare && _lifeEnabled,
                        onChanged: _lifeEnabled
                            ? (v) {
                                setState(() => _lifeShare = v);
                                _update('life_share_enabled', v);
                              }
                            : null,
                      ),
                      Padding(
                        padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(l10n.lifeIntensity,
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                            const SizedBox(height: 2),
                            Text(l10n.lifeIntensityHint,
                                style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                            const SizedBox(height: 8),
                            SegmentedButton<String>(
                              segments: [
                                ButtonSegment(value: 'low', label: Text(l10n.low)),
                                ButtonSegment(value: 'medium', label: Text(l10n.medium)),
                                ButtonSegment(value: 'high', label: Text(l10n.high)),
                              ],
                              selected: {_lifeIntensity},
                              onSelectionChanged: _lifeEnabled
                                  ? (sel) {
                                      setState(() => _lifeIntensity = sel.first);
                                      _update('life_intensity', sel.first);
                                    }
                                  : null,
                              showSelectedIcon: false,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  _expansionSwitch(
                    icon: Icons.visibility_outlined,
                    title: l10n.checkIn,
                    subtitle: l10n.checkInHint,
                    value: _checkIn,
                    onChanged: (v) {
                      setState(() => _checkIn = v);
                      _update('check_in_enabled', v);
                    },
                    children: [
                      _childSwitch(
                        title: l10n.control,
                        subtitle: l10n.controlHint,
                        value: false,
                        onChanged: (v) {
                          ScaffoldMessenger.of(context)
                            ..hideCurrentSnackBar()
                            ..showSnackBar(SnackBar(
                              content: Text(l10n.controlComingSoon),
                              duration: const Duration(seconds: 2),
                            ));
                        },
                      ),
                    ],
                  ),
                ]),
                _auroraGroup(title: l10n.creationGroup, children: [
                  _expansionSwitch(
                    icon: Icons.auto_awesome,
                    title: l10n.imageGen,
                    subtitle: l10n.imageGenHint,
                    value: _imageGen,
                    onChanged: (v) {
                      setState(() {
                        _imageGen = v;
                        if (!v) _activeImageGen = false;
                      });
                      _update('image_gen_enabled', v);
                      if (!v) _update('active_image_gen_enabled', false);
                    },
                    children: [
                      _childSwitch(
                        title: l10n.activeImageGen,
                        subtitle: l10n.activeImageGenHint,
                        value: _activeImageGen && _imageGen,
                        onChanged: _imageGen
                            ? (v) {
                                setState(() => _activeImageGen = v);
                                _update('active_image_gen_enabled', v);
                              }
                            : null,
                      ),
                    ],
                  ),
                ]),
                _auroraGroup(title: l10n.worldGroup, children: [
                  ListTile(
                    leading: _settingsRowIcon(context, Icons.menu_book_outlined, active: true),
                    title: Text(l10n.lorebookTitle, style: const TextStyle(fontSize: 15)),
                    subtitle: Text(l10n.lorebookHint,
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                    trailing: const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => LorebookScreen(characterId: widget.characterId)),
                    ),
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  ListTile(
                    leading: _settingsRowIcon(context, Icons.public_outlined, active: true),
                    title: Text(l10n.worldFactsTitle, style: const TextStyle(fontSize: 15)),
                    subtitle: Text(l10n.worldFactsHint,
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                    trailing: const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => WorldSettingsScreen(characterId: widget.characterId)),
                    ),
                  ),
                ]),
                _auroraGroup(title: l10n.socialGroup, children: [
                  _groupSwitchTile(
                    icon: Icons.psychology_outlined,
                    title: l10n.cognitiveLoop,
                    subtitle: l10n.cognitiveLoopHint,
                    value: _cognitiveLoop,
                    onChanged: _onCognitiveLoopChanged,
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  _groupSwitchTile(
                    icon: Icons.all_inclusive,
                    title: l10n.weaveFullInject,
                    subtitle: l10n.weaveFullInjectHint,
                    value: _weaveFullInject,
                    onChanged: (v) {
                      setState(() => _weaveFullInject = v);
                      _update('weave_full_inject_enabled', v);
                    },
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  _expansionSwitch(
                    icon: Icons.notifications_active_outlined,
                    title: l10n.proactiveChat,
                    subtitle: l10n.proactiveChatHint,
                    value: _proactive,
                    onChanged: (v) {
                      setState(() => _proactive = v);
                      _update('enable_proactive', v);
                    },
                    children: [
                      _childSwitch(
                        title: l10n.memoryReview,
                        subtitle: l10n.memoryReviewHint,
                        value: _memoryReview && _proactive,
                        onChanged: _proactive
                            ? (v) {
                                setState(() => _memoryReview = v);
                                _update('memory_review_enabled', v);
                              }
                            : null,
                      ),
                      if (_proactive) ...[
                        Padding(
                          padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(l10n.proactiveFrequency,
                                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                              const SizedBox(height: 2),
                              Text(l10n.proactiveFrequencyHint,
                                  style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                              const SizedBox(height: 8),
                              SegmentedButton<String>(
                                segments: [
                                  ButtonSegment(value: 'low', label: Text(l10n.lowFreq)),
                                  ButtonSegment(value: 'medium', label: Text(l10n.standard)),
                                  ButtonSegment(value: 'high', label: Text(l10n.highFreq)),
                                ],
                                selected: {_frequency},
                                onSelectionChanged: (s) {
                                  setState(() => _frequency = s.first);
                                  _update('frequency', s.first);
                                },
                                showSelectedIcon: false,
                              ),
                            ],
                          ),
                        ),
                        _childSwitch(
                          title: l10n.dndPeriod,
                          subtitle: _dndEnabled
                              ? l10n.dndOn(_dndStart, _dndEnd)
                              : l10n.dndOff,
                          value: _dndEnabled,
                          onChanged: _proactive
                              ? (v) {
                                  setState(() => _dndEnabled = v);
                                  _update('dnd_enabled', v);
                                }
                              : null,
                        ),
                        if (_dndEnabled) ...[
                          _timeRow(l10n.start, _dndStart, 'dnd_start'),
                          _timeRow(l10n.end, _dndEnd, 'dnd_end'),
                        ],
                      ],
                    ],
                  ),
                  Divider(height: 1, indent: 52, color: scheme.outlineVariant),
                  _expansionSwitch(
                    icon: Icons.people_outline,
                    title: l10n.moments,
                    subtitle: l10n.momentsHint,
                    value: _moments,
                    onChanged: (v) {
                      setState(() {
                        _moments = v;
                        if (!v) _momentsComment = false;
                      });
                      _update('moments_enabled', v);
                      if (!v) _update('moments_comment_enabled', false);
                    },
                    children: [
                      _childSwitch(
                        title: l10n.momentsComment,
                        subtitle: l10n.momentsCommentHint,
                        value: _momentsComment && _moments,
                        onChanged: _moments
                            ? (v) {
                                setState(() => _momentsComment = v);
                                _update('moments_comment_enabled', v);
                              }
                            : null,
                      ),
                    ],
                  ),
                ]),
                // 隐私（组）：总开关 = 隐私；子开关 = 隐私上锁 + 思考过程（三挡）+ 调用能力
                _auroraGroup(title: l10n.privacyGroup, children: [
                  _expansionSwitch(
                    icon: Icons.lock_outline,
                    title: l10n.privacy,
                    subtitle: l10n.privacyHint,
                    value: _privacyEnabled,
                    onChanged: (v) {
                      setState(() {
                        _privacyEnabled = v;
                        if (!v) {
                          _privacyLock = false;
                          _reasoningLevel = 0;
                          _showTools = false;
                        }
                      });
                      _update('privacy_enabled', v);
                      if (!v) {
                        _update('privacy_lock_enabled', false);
                        _update('reasoning_level', 0);
                        _update('show_tools_enabled', false);
                      }
                    },
                    children: [
                      _childSwitch(
                        title: l10n.privacyLock,
                        subtitle: l10n.privacyLockHint,
                        value: _privacyLock,
                        onChanged: _privacyEnabled
                            ? (v) {
                                setState(() => _privacyLock = v);
                                _update('privacy_lock_enabled', v);
                              }
                            : null,
                      ),
                      _childSwitch(
                        title: l10n.showTools,
                        subtitle: l10n.showToolsHint,
                        value: _showTools,
                        onChanged: _privacyEnabled
                            ? (v) {
                                setState(() => _showTools = v);
                                _update('show_tools_enabled', v);
                              }
                            : null,
                      ),
                      Padding(
                        padding: const EdgeInsets.only(left: 8, top: 6, bottom: 8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(l10n.reasoningLevel,
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                            const SizedBox(height: 2),
                            Text(l10n.reasoningLevelHint,
                                style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                            const SizedBox(height: 8),
                            SegmentedButton<int>(
                              segments: [
                                ButtonSegment(value: 0, label: Text(l10n.off), icon: const Icon(Icons.visibility_off_outlined, size: 16)),
                                ButtonSegment(value: 1, label: Text(l10n.simpleThinking), icon: const Icon(Icons.lightbulb_outline, size: 16)),
                                ButtonSegment(value: 2, label: Text(l10n.deepThinking), icon: const Icon(Icons.psychology_outlined, size: 16)),
                              ],
                              selected: {_reasoningLevel},
                              onSelectionChanged: _privacyEnabled
                                  ? (s) {
                                      final v = s.first;
                                      setState(() => _reasoningLevel = v);
                                      _update('reasoning_level', v);
                                    }
                                  : null,
                              showSelectedIcon: false,
                              style: const ButtonStyle(visualDensity: VisualDensity.compact),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ]),
                // 状态（总开关 + 展开）：状态触发 → 冷战断联 / 心情标识
                _auroraGroup(title: l10n.statusGroup, children: [
                  _expansionSwitch(
                    icon: Icons.mood_bad_outlined,
                    title: l10n.status,
                    subtitle: l10n.statusHint,
                    value: _stateTrigger,
                    onChanged: _onStateChanged,
                    children: [
                      _childSwitch(
                        title: l10n.stateTrigger,
                        subtitle: l10n.stateTriggerHint,
                        value: _stateTrigger,
                        onChanged: _stateTrigger ? _onStateChanged : null,
                      ),
                      _childSwitch(
                        title: l10n.coldWar,
                        subtitle: l10n.coldWarHint,
                        value: _coldWar && _stateTrigger,
                        onChanged: (_stateTrigger)
                            ? (v) {
                                setState(() => _coldWar = v);
                                _update('cold_war_enabled', v);
                              }
                            : null,
                      ),
                      _childSwitch(
                        title: l10n.moodBadge,
                        subtitle: l10n.moodBadgeHint,
                        value: _moodBadge,
                        onChanged: (v) {
                          setState(() => _moodBadge = v);
                          _update('mood_badge_enabled', v);
                        },
                      ),
                    ],
                  ),
                ]),
              ],
            ),
    );
  }

  /// 普通开关行
  Widget _groupSwitchTile({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return InkWell(
      onTap: () => onChanged(!value),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
          children: [
            _settingsRowIcon(context, icon, active: value),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w500, color: Theme.of(context).colorScheme.onSurface)),
                  const SizedBox(height: 1),
                  Text(subtitle,
                      style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                ],
              ),
            ),
            Switch(value: value, onChanged: onChanged),
          ],
        ),
      ),
    );
  }

  /// 可展开的父开关（点击开关切换、点击行展开子项；开关左侧带无柄箭头，仿手机感知）
  Widget _expansionSwitch({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool>? onChanged,
    required List<Widget> children,
  }) {
    final scheme = Theme.of(context).colorScheme;
    final isOpen = _expanded[title] ?? false;
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        leading: _settingsRowIcon(context, icon, active: value),
        title: Text(title,
            style: TextStyle(
                fontSize: 15, fontWeight: FontWeight.w500, color: scheme.onSurface)),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        childrenPadding: const EdgeInsets.only(left: 16, right: 16, bottom: 8),
        tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 0),
        shape: const Border(),
        collapsedShape: const Border(),
        onExpansionChanged: (v) => setState(() => _expanded[title] = v),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(isOpen ? Icons.expand_less : Icons.expand_more,
                size: 20, color: AppColors.separator),
            Switch(value: value, onChanged: onChanged),
          ],
        ),
        children: children,
      ),
    );
  }

  /// 免打扰时段行：点击弹出时间选择
  Widget _timeRow(String label, String value, String field) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(left: 8, top: 4, bottom: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => _pickTime(value, field),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          child: Row(
            children: [
              Text(label,
                  style: const TextStyle(fontSize: 14, color: IosCardColors.subtitle)),
              const Spacer(),
              Text(value,
                  style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                      color: scheme.onSurface)),
              const SizedBox(width: 4),
              Icon(Icons.chevron_right, size: 18, color: scheme.onSurface.withValues(alpha: 0.4)),
            ],
          ),
        ),
      ),
    );
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

  /// 子开关行（父开关关闭时 onChanged 为 null：标题自动灰化）
  Widget _childSwitch({
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool>? onChanged,
  }) {
    final scheme = Theme.of(context).colorScheme;
    final enabled = onChanged != null;
    return Padding(
      padding: const EdgeInsets.only(top: 2, bottom: 2),
      child: SwitchListTile(
        contentPadding: const EdgeInsets.only(left: 8),
        title: Text(title,
            style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        value: value,
        onChanged: onChanged,
        activeThumbColor: Theme.of(context).colorScheme.primary,
      ),
    );
  }
}


/// Aurora P5 分组：AuroraCard 版 IosCardGroup（标题视觉保留；透明 Material 防组内开关断言）
Widget _auroraGroup({required String title, required List<Widget> children}) {
  return Padding(
    padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(left: 16, bottom: 6),
          child: Text(title,
              style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: IosCardColors.subtitle)),
        ),
        AuroraCard(
          padding: EdgeInsets.zero,
          child: Material(
            type: MaterialType.transparency,
            child: Column(children: children),
          ),
        ),
      ],
    ),
  );
}

/// Aurora P5 行图标：40×40 圆角 12 容器（主题色 0.10~0.14 底，激活时图标主题色）
Widget _settingsRowIcon(BuildContext context, IconData icon, {required bool active}) {
  final scheme = Theme.of(context).colorScheme;
  return Container(
    width: 40,
    height: 40,
    decoration: BoxDecoration(
      color: scheme.primary.withValues(alpha: active ? 0.14 : 0.10),
      borderRadius: BorderRadius.circular(12),
    ),
    child: Icon(icon, size: 22, color: active ? scheme.primary : IosCardColors.subtitle),
  );
}
