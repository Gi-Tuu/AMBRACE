import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';

import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../widgets/avatar_crop_screen.dart';
import '../../widgets/ios_card_group.dart';
import '../chat/archive_screen.dart';
import 'agent_mind_screen.dart';
import 'character_settings_screen.dart';
import '../diary/diary_screen.dart';
import '../memory/memory_book_screen.dart';
import '../life/life_home_screen.dart';
import '../memory/timeline_screen.dart';
import '../state/visual_state_screen.dart';

/// 角色详情页（UI 2.0：iOS 分组卡片 + 头像裁剪更换）
class CharacterDetailScreen extends StatefulWidget {
  final AICharacter character;
  final int sessionId;

  const CharacterDetailScreen({
    super.key,
    required this.character,
    required this.sessionId,
  });

  @override
  State<CharacterDetailScreen> createState() => _CharacterDetailScreenState();
}

class _CharacterDetailScreenState extends State<CharacterDetailScreen> {
  final _api = ApiClient();
  bool _diaryEnabled = true;
  bool _momentsEnabled = true;
  bool _loadingSettings = true;
  bool _publishingMoment = false;
  String? _avatarUrl;
  bool _uploadingAvatar = false;
  bool _bioExpanded = false;
  bool _selfStmtExpanded = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
    _avatarUrl = widget.character.avatarUrl;
  }

  Future<void> _publishMoment() async {
    if (_publishingMoment) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _publishingMoment = true);
    try {
      final result = await _api.publishMoment(widget.character.id);
      if (result['success'] == true) {
        final moment = result['moment'] as Map<String, dynamic>;
        final text = moment['content']?.toString() ?? '';
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.momentPublished(text.substring(0, text.length.clamp(0, 20))))),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        String msg = e.toString();
        if (msg.contains('400')) msg = l10n.momentLimit;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.momentPublishFailed(msg))));
      }
    }
    if (mounted) setState(() => _publishingMoment = false);
  }

  /// 选图 → 圆形裁剪范围 → 上传头像
  Future<void> _changeAvatar() async {
    final l10n = AppLocalizations.of(context)!;
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1920,
      imageQuality: 90,
    );
    if (picked == null || !mounted) return;
    final bytes = await picked.readAsBytes();
    if (!mounted) return;
    final cropped = await Navigator.of(context).push<Uint8List>(
      MaterialPageRoute(builder: (_) => AvatarCropScreen(imageBytes: bytes)),
    );
    if (cropped == null || !mounted) return;
    setState(() => _uploadingAvatar = true);
    File? tmp;
    try {
      tmp = File(
          '${Directory.systemTemp.path}/avatar_crop_${DateTime.now().millisecondsSinceEpoch}.png');
      await tmp.writeAsBytes(cropped);
      final up = await _api.uploadAvatar(tmp);
      final url = up["url"] as String? ?? "";
      if (url.isEmpty) throw Exception("empty url");
      await _api.updateCharacter(widget.character.id, {"avatar_url": url});
      if (mounted) {
        setState(() => _avatarUrl = url);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.avatarUpdated)));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.avatarUpdateFailed)));
      }
    } finally {
      if (tmp != null && tmp.existsSync()) tmp.deleteSync();
      if (mounted) setState(() => _uploadingAvatar = false);
    }
  }

  void _showMemoryList() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => MemoryBookScreen(
          characterId: widget.character.id,
          characterName: widget.character.name,
        ),
      ),
    );
  }

  void _openVisualState() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => VisualStateScreen(
          characterId: widget.character.id,
          characterName: widget.character.name,
        ),
      ),
    );
  }

  void _showLifeSheet() {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (sheetCtx) {
        Widget lifeRow(IconData icon, String title, String subtitle, VoidCallback onTap) {
          return InkWell(
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              child: Row(
                children: [
                  Icon(icon, size: 22, color: scheme.primary),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title,
                            style: TextStyle(fontSize: 15, color: scheme.onSurface)),
                        const SizedBox(height: 1),
                        Text(subtitle,
                            style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                      ],
                    ),
                  ),
                  const Icon(Icons.chevron_right, size: 18, color: IosCardColors.chevron),
                ],
              ),
            ),
          );
        }

        return Container(
          decoration: BoxDecoration(
            color: scheme.surface,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
          ),
          child: SafeArea(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const SizedBox(height: 10),
                Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Theme.of(context).dividerColor,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(height: 14),
                Text(l10n.charLife,
                    style: TextStyle(
                        fontSize: 17, fontWeight: FontWeight.w600, color: scheme.onSurface)),
                const SizedBox(height: 8),
                IosCardGroup(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                  children: [
                    if (!_loadingSettings && _diaryEnabled)
                      lifeRow(Icons.book_outlined, l10n.diary, l10n.diaryHint, () {
                        Navigator.pop(sheetCtx);
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => DiaryScreen(
                              characterId: widget.character.id,
                              characterName: widget.character.name,
                            ),
                          ),
                        );
                      }),
                    const IosCardDivider(),
                    lifeRow(Icons.self_improvement_outlined, l10n.aiLife, l10n.aiLifeHint, () {
                      Navigator.pop(sheetCtx);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => LifeHomeScreen(
                            characterId: widget.character.id,
                            characterName: widget.character.name,
                          ),
                        ),
                      );
                    }),
                    const IosCardDivider(),
                    lifeRow(Icons.auto_stories_outlined, l10n.memoryBook, l10n.memoryBookHint, () {
                      Navigator.pop(sheetCtx);
                      _showMemoryList();
                    }),
                    const IosCardDivider(),
                    lifeRow(Icons.timeline_outlined, l10n.timeline, l10n.timelineHint, () {
                      Navigator.pop(sheetCtx);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => TimelineScreen(
                            characterId: widget.character.id,
                            characterName: widget.character.name,
                          ),
                        ),
                      );
                    }),
                    const IosCardDivider(),
                    lifeRow(Icons.inventory_2_outlined, l10n.chatArchive, l10n.chatArchiveHint, () {
                      Navigator.pop(sheetCtx);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => ArchiveScreen(
                            characterName: widget.character.name,
                            sessionId: widget.sessionId,
                          ),
                        ),
                      );
                    }),
                    if (!_loadingSettings && _momentsEnabled) ...[
                      const IosCardDivider(),
                      lifeRow(Icons.radio_button_unchecked, l10n.manualMoment, l10n.manualMomentHint, () {
                        Navigator.pop(sheetCtx);
                        _publishMoment();
                      }),
                    ],
                  ],
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _loadSettings() async {
    try {
      final data = await _api.getSchedulerSettings(widget.character.id);
      if (mounted) {
        setState(() {
          _diaryEnabled = data['diary_enabled'] as bool? ?? true;
          _momentsEnabled = data['moments_enabled'] as bool? ?? true;
          _loadingSettings = false;
        });
      }
    } catch (e) {
      debugPrint("Diary settings load error: ");
      if (mounted) setState(() => _loadingSettings = false);
    }
  }

  /// 多段文本按换行分段渲染（保留段落结构；忽略空段）。
  /// 自述（self_statement）为自动合并的多段内容，历史数据用「；」分段，一并切分便于阅读。
  Widget _bodyText(String text, TextStyle style, {bool splitSemicolon = false}) {
    final raw = text.replaceAll('\r', '').trim();
    if (raw.isEmpty) return Text('', style: style);
    var parts = raw.split('\n');
    if (splitSemicolon) {
      parts = parts.expand((p) => p.split('；')).toList();
    }
    final paras = parts.map((p) => p.trim()).where((p) => p.isNotEmpty).toList();
    if (paras.isEmpty) return Text('', style: style);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < paras.length; i++) ...[
          if (i > 0) const SizedBox(height: 8),
          Text(paras[i], style: style),
        ],
      ],
    );
  }

  /// 正文折叠阈值：超过该字数时折叠为预览 + 「展开/收起」（与后端自述长度控制一致，正文 ≤200 字）。
  static const int _kBodyCollapseLimit = 200;

  /// 正文区块：超过 _kBodyCollapseLimit 字时折叠为预览并显示「展开/收起」；
  /// 折叠只截断预览文本，展开后展示完整内容；分段结构由 _bodyText 保留。
  Widget _collapsibleBodyText(
    String text,
    TextStyle style, {
    bool splitSemicolon = false,
    required bool expanded,
    required VoidCallback onToggle,
  }) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final raw = text.replaceAll('\r', '').trim();
    final needsToggle = raw.length > _kBodyCollapseLimit;
    final shown = (needsToggle && !expanded) ? _truncatePreview(raw) : raw;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _bodyText(shown, style, splitSemicolon: splitSemicolon),
        if (needsToggle) ...[const SizedBox(height: 6),
          InkWell(
            onTap: onToggle,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  expanded ? l10n.collapse : l10n.expand,
                  style: TextStyle(fontSize: 13, color: scheme.primary),
                ),
                const SizedBox(width: 2),
                Icon(
                  expanded ? Icons.expand_less : Icons.expand_more,
                  size: 16,
                  color: scheme.primary,
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }

  /// 折叠预览：截到 _kBodyCollapseLimit 字，优先在末尾窗口内的段落/句号边界收尾，避免截破一句话。
  String _truncatePreview(String raw) {
    if (raw.length <= _kBodyCollapseLimit) return raw;
    var end = _kBodyCollapseLimit;
    final windowStart = _kBodyCollapseLimit > 40 ? _kBodyCollapseLimit - 40 : 0;
    final window = raw.substring(windowStart, end);
    var cut = -1;
    for (var i = window.length - 1; i >= 0; i--) {
      final c = window[i];
      if (c == '\n' || c == '。' || c == '！' || c == '？' || c == '；' || c == '，') {
        cut = i;
        break;
      }
    }
    if (cut >= 0) end = windowStart + cut + 1;
    return '${raw.substring(0, end).trimRight()}…';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final char = widget.character;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(char.name),
            const SizedBox(width: 6),
            Text(
              '#${char.id}',
              style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle, fontWeight: FontWeight.normal),
            ),
          ],
        ),
        actions: [
          // 角色生活入口：日记 / 记忆本 / 时光 / 聊天记录箱 / 手动发朋友圈
          TextButton.icon(
            icon: const Icon(Icons.auto_awesome, size: 18),
            label: Text(l10n.charLife),
            style: TextButton.styleFrom(foregroundColor: scheme.primary),
            onPressed: _showLifeSheet,
          ),
          // AI 内心世界入口（Phase J/P1）：复盘 / 任务 / 工具轨迹
          IconButton(
            icon: const Icon(Icons.psychology_alt_outlined),
            tooltip: l10n.agentMind,
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => AgentMindScreen(
                    characterId: char.id,
                    characterName: char.name,
                  ),
                ),
              );
            },
          ),
          // 设置按钮
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: l10n.settingsTitle,
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => CharacterSettingsScreen(
                    characterId: char.id,
                    characterName: char.name,
                  ),
                ),
              );
            },
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.only(top: 16, bottom: 24),
        children: [
          _profileHeader(char),
          if ((char.gender != null && char.gender!.isNotEmpty) ||
              (char.birthday != null && char.birthday!.isNotEmpty) ||
              char.height != null ||
              char.weight != null)
            IosCardGroup(title: l10n.basic, children: [
              if (char.gender != null && char.gender!.isNotEmpty)
                _infoTile(Icons.wc, l10n.gender, char.gender!),
              if ((char.gender != null && char.gender!.isNotEmpty) &&
                  (char.birthday != null && char.birthday!.isNotEmpty))
                const IosCardDivider(),
              if (char.birthday != null && char.birthday!.isNotEmpty)
                _infoTile(Icons.cake, l10n.birthday, char.birthday!),
              if ((char.birthday != null && char.birthday!.isNotEmpty) &&
                  char.height != null)
                const IosCardDivider(),
              if (char.height != null) _infoTile(Icons.straighten, l10n.height, '${char.height} cm'),
              if (char.height != null && char.weight != null) const IosCardDivider(),
              if (char.weight != null) _infoTile(Icons.monitor_weight, l10n.weight, '${char.weight} kg'),
            ]),
          if ((char.personality != null && char.personality!.isNotEmpty) ||
              (char.appearance != null && char.appearance!.isNotEmpty) ||
              (char.chatStyle != null && char.chatStyle!.isNotEmpty))
            IosCardGroup(title: l10n.portraitGroup, children: [
              if (char.personality != null && char.personality!.isNotEmpty)
                _infoTile(Icons.psychology, l10n.personality, char.personality!),
              if (char.appearance != null && char.appearance!.isNotEmpty) ...[
                const IosCardDivider(),
                _infoTile(Icons.face_retouching_natural, l10n.appearance, char.appearance!),
              ],
              if (char.chatStyle != null && char.chatStyle!.isNotEmpty) ...[
                const IosCardDivider(),
                _infoTile(Icons.chat, l10n.chatStyle, char.chatStyle!),
              ],
            ]),
          if (char.currentStatus != null && char.currentStatus!.isNotEmpty)
            IosCardGroup(title: l10n.status, children: [
              InkWell(
                onTap: _openVisualState,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                  child: Row(
                    children: [
                      Icon(Icons.wb_sunny_outlined, size: 20, color: scheme.primary),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          char.currentStatus!,
                          style: TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface),
                        ),
                      ),
                      Text(l10n.visualize,
                          style: TextStyle(fontSize: 12, color: scheme.primary)),
                      const Icon(Icons.chevron_right, size: 18, color: IosCardColors.chevron),
                    ],
                  ),
                ),
              ),
            ]),
          if (char.bio != null && char.bio!.isNotEmpty)
            IosCardGroup(title: l10n.backgroundInfo, children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.person_outline, size: 20, color: scheme.primary),
                    const SizedBox(width: 12),
                    Expanded(
                      child: _collapsibleBodyText(
                        char.bio!,
                        TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface),
                        expanded: _bioExpanded,
                        onToggle: () => setState(() => _bioExpanded = !_bioExpanded),
                      ),
                    ),
                  ],
                ),
              ),
            ]),
          IosCardGroup(title: l10n.selfStatement, children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.auto_awesome, size: 20, color: scheme.primary),
                  const SizedBox(width: 12),
                  Expanded(
                    child: (char.selfStatement != null && char.selfStatement!.isNotEmpty)
                        ? _collapsibleBodyText(
                            char.selfStatement!,
                            TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface),
                            splitSemicolon: true,
                            expanded: _selfStmtExpanded,
                            onToggle: () => setState(() => _selfStmtExpanded = !_selfStmtExpanded),
                          )
                        : Text(
                            l10n.noSelfStatement,
                            style: const TextStyle(fontSize: 14, height: 1.5, color: IosCardColors.subtitle),
                          ),
                  ),
                ],
              ),
            ),
          ]),
        ],
      ),
    );
  }

  Widget _profileHeader(AICharacter char) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 20),
      child: Column(
        children: [
          GestureDetector(
            onTap: _uploadingAvatar ? null : _changeAvatar,
            child: Stack(
              children: [
                CircleAvatar(
                  radius: 48,
                  backgroundColor: scheme.secondaryContainer,
                  child: _avatarUrl != null && _avatarUrl!.isNotEmpty
                      ? ClipOval(
                          child: Image.network(
                            _api.resolveUrl(_avatarUrl!),
                            width: 96,
                            height: 96,
                            fit: BoxFit.cover,
                            errorBuilder: (context, error, stack) => Text(
                              char.name.isNotEmpty ? char.name[0] : '?',
                              style: const TextStyle(fontSize: 40),
                            ),
                          ),
                        )
                      : Text(
                          char.name.isNotEmpty ? char.name[0] : '?',
                          style: const TextStyle(fontSize: 40),
                        ),
                ),
                if (_uploadingAvatar)
                  const Positioned.fill(
                    child: Center(child: CircularProgressIndicator()),
                  ),
                Positioned(
                  right: 2,
                  bottom: 2,
                  child: Container(
                    padding: const EdgeInsets.all(5),
                    decoration: BoxDecoration(
                      color: scheme.primary,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(Icons.camera_alt, size: 14, color: scheme.onPrimary),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Text(char.name,
              style: TextStyle(
                  fontSize: 20, fontWeight: FontWeight.w700, color: scheme.onSurface)),
          const SizedBox(height: 2),
          Text('#${char.id}',
              style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          if (char.personality != null && char.personality!.isNotEmpty) ...[
            const SizedBox(height: 10),
            _personalityChips(char.personality!),
          ],
          const SizedBox(height: 8),
          Text(l10n.tapAvatarToChange,
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        ],
      ),
    );
  }

  Widget _personalityChips(String personality) {
    final scheme = Theme.of(context).colorScheme;
    final tags = personality
        .split(RegExp(r'[、，,;/；\s]+'))
        .map((t) => t.trim())
        .where((t) => t.isNotEmpty)
        .take(4)
        .toList();
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      alignment: WrapAlignment.center,
      children: [
        for (final t in tags)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: scheme.secondaryContainer.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Text(t,
                style: TextStyle(fontSize: 11, color: scheme.onSecondaryContainer)),
          ),
      ],
    );
  }

  Widget _infoTile(IconData icon, String label, String value) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: scheme.primary),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label,
                    style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                const SizedBox(height: 2),
                Text(value,
                    style: TextStyle(fontSize: 15, color: scheme.onSurface)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
