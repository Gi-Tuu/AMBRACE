import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';

import '../../models/character.dart';
import '../../models/character_state.dart';
import '../../services/api_client.dart';
import '../../theme/tokens.dart';
import '../../widgets/avatar_crop_screen.dart';
import '../../widgets/ios_card_group.dart';
import '../../widgets/character_bubble_overlay.dart';
import '../../widgets/character_calendar_faces.dart';
import '../../widgets/character_entry_carousel.dart';
import '../chat/archive_screen.dart';
import 'agent_mind_screen.dart';
import 'character_settings_screen.dart';
import '../diary/diary_screen.dart';
import '../memory/memory_book_screen.dart';
import '../life/life_home_screen.dart';
import '../memory/timeline_screen.dart';
import '../state/visual_state_screen.dart';
import '../weave/weave_library_screen.dart';
import '../../widgets/app_page_route.dart';

/// 角色详情页 v3：头像气泡 + 入口翻转轮播 + 三面日历 + 记忆气泡。
///
/// 交互：
/// - 点击头像：裁剪更换
/// - 长按头像：弹出气泡浮层（标签/基本/形象/自述）
/// - 左右滑动头像：与下方日历同款 PageView 透视翻转，切换
///   设置 / AI 内心世界 / AI 生活 三个入口（点击入口卡片进入，返回吸附回头像）
/// - 三面翻转：日记日历 / 状态 / 记忆库
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

  // 状态数据
  CharacterState? _charState;

  // 日记缓存
  final Map<String, String?> _diaryCache = {};

  @override
  void initState() {
    super.initState();
    _avatarUrl = widget.character.avatarUrl;
    _loadSettings();
    _loadCharState();
  }

  @override
  void dispose() {
    super.dispose();
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
    } catch (_) {
      if (mounted) setState(() => _loadingSettings = false);
    }
  }

  Future<void> _loadCharState() async {
    try {
      final s = await _api.getCharacterStates(widget.character.id);
      if (mounted) setState(() => _charState = s);
    } catch (_) {}
  }

  // ── 头像更换 ──

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
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.avatarUpdated)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.avatarUpdateFailed)));
      }
    } finally {
      if (tmp != null && tmp.existsSync()) tmp.deleteSync();
      if (mounted) setState(() => _uploadingAvatar = false);
    }
  }

  // ── 长按气泡浮层 ──

  void _openBubbleOverlay() {
    final l10n = AppLocalizations.of(context)!;
    final char = widget.character;
    final scheme = Theme.of(context).colorScheme;

    final tags = (char.personality ?? '')
        .split(RegExp(r'[、，,;/；\s]+'))
        .map((t) => t.trim())
        .where((t) => t.isNotEmpty)
        .take(6)
        .toList();

    final basicParts = <String>[];
    if (char.gender != null && char.gender!.isNotEmpty) {
      basicParts.add('${l10n.gender}：${char.gender}');
    }
    if (char.birthday != null && char.birthday!.isNotEmpty) {
      basicParts.add('${l10n.birthday}：${char.birthday}');
    }
    if (char.height != null) basicParts.add('${l10n.height}：${char.height} cm');
    if (char.weight != null) basicParts.add('${l10n.weight}：${char.weight} kg');

    final cards = <BubbleCardData>[
      BubbleCardData(
        icon: Icons.label_outline,
        title: l10n.personality,
        preview: tags.isEmpty ? l10n.noTags : tags.join('、'),
        detailBuilder: (_) => Wrap(
          spacing: 8,
          runSpacing: 8,
          children: tags
              .map((t) => Chip(
                    label: Text(t),
                    backgroundColor: scheme.secondaryContainer,
                    labelStyle:
                        TextStyle(color: scheme.onSecondaryContainer, fontSize: 13),
                  ))
              .toList(),
        ),
      ),
      BubbleCardData(
        icon: Icons.info_outline,
        title: l10n.basic,
        preview: basicParts.isEmpty ? l10n.noBasicInfo : basicParts.first,
        detailBuilder: (_) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final p in basicParts) ...[
              Text(p, style: TextStyle(fontSize: 14, color: scheme.onSurface)),
              const SizedBox(height: 10),
            ],
            if (basicParts.isEmpty)
              Text(l10n.noBasicInfo,
                  style: TextStyle(color: AppColors.textSecondary)),
          ],
        ),
      ),
      BubbleCardData(
        icon: Icons.face_retouching_natural,
        title: l10n.portraitGroup,
        preview: char.appearance ?? char.personality ?? l10n.noAppearanceDesc,
        detailBuilder: (_) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (char.personality != null && char.personality!.isNotEmpty) ...[
              Text(l10n.personality,
                  style: TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 4),
              Text(char.personality!,
                  style: TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface)),
              const SizedBox(height: 14),
            ],
            if (char.appearance != null && char.appearance!.isNotEmpty) ...[
              Text(l10n.appearance,
                  style: TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 4),
              Text(char.appearance!,
                  style: TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface)),
              const SizedBox(height: 14),
            ],
            if (char.chatStyle != null && char.chatStyle!.isNotEmpty) ...[
              Text(l10n.chatStyle,
                  style: TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 4),
              Text(char.chatStyle!,
                  style: TextStyle(fontSize: 14, height: 1.5, color: scheme.onSurface)),
            ],
          ],
        ),
      ),
      BubbleCardData(
        icon: Icons.auto_awesome,
        title: l10n.selfStatement,
        preview: char.selfStatement ?? char.bio ?? l10n.noSelfStatement,
        detailBuilder: (_) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (char.bio != null && char.bio!.isNotEmpty) ...[
              Text(l10n.backgroundInfo,
                  style: TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 4),
              Text(char.bio!,
                  style: TextStyle(fontSize: 14, height: 1.6, color: scheme.onSurface)),
              const SizedBox(height: 14),
            ],
            if (char.selfStatement != null &&
                char.selfStatement!.isNotEmpty) ...[
              Text(l10n.selfStatement,
                  style: TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 4),
              Text(char.selfStatement!,
                  style: TextStyle(fontSize: 14, height: 1.6, color: scheme.onSurface)),
            ],
            if ((char.bio == null || char.bio!.isEmpty) &&
                (char.selfStatement == null || char.selfStatement!.isEmpty))
              Text(l10n.noSelfStatement,
                  style: TextStyle(color: AppColors.textSecondary)),
          ],
        ),
      ),
    ];

    CharacterBubbleOverlay.show(
      context,
      avatar: _buildAvatarImage(144.0),
      cards: cards,
    );
  }

  // ── 头像入口翻转轮播：三个入口 ──

  void _openSettings() {
    final char = widget.character;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CharacterSettingsScreen(
          characterId: char.id,
          characterName: char.name,
        ),
      ),
    );
  }

  /// AI 内心世界（v3 从「角色生活 Sheet」提升为独立入口）。
  void _openAgentMind() {
    final char = widget.character;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => AgentMindScreen(
          characterId: char.id,
          characterName: char.name,
        ),
      ),
    );
  }

  /// AI 生活：头像轮播入口直达 LifeHomeScreen（替代旧的「角色生活」聚合 Sheet）
  void _openAiLife() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => LifeHomeScreen(
          characterId: widget.character.id,
          characterName: widget.character.name,
        ),
      ),
    );
  }

  // ── 朋友圈 ──

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
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(l10n.momentPublished(
                  text.substring(0, text.length.clamp(0, 20))))));
        }
      }
    } catch (e) {
      if (mounted) {
        String msg = e.toString();
        if (msg.contains('400')) msg = l10n.momentLimit;
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.momentPublishFailed(msg))));
      }
    }
    if (mounted) setState(() => _publishingMoment = false);
  }

  // ── 角色生活 Sheet ──

  // 保留：头像轮播已改直达「AI 生活」，此聚合 Sheet 暂无用例但仍保留（后续确认无入口再删）。
  // ignore: unused_element
  void _showLifeSheet() {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (sheetCtx) {
        Widget lifeRow(IconData icon, String title, String subtitle,
            VoidCallback onTap) {
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
                            style: TextStyle(
                                fontSize: 15, color: scheme.onSurface)),
                        const SizedBox(height: 1),
                        Text(subtitle,
                            style: const TextStyle(
                                fontSize: 11,
                                color: IosCardColors.subtitle)),
                      ],
                    ),
                  ),
                  const Icon(Icons.chevron_right,
                      size: 18, color: IosCardColors.chevron),
                ],
              ),
            ),
          );
        }

        return Container(
          decoration: BoxDecoration(
            color: scheme.surface,
            borderRadius:
                const BorderRadius.vertical(top: Radius.circular(16)),
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
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                        color: scheme.onSurface)),
                const SizedBox(height: 8),
                IosCardGroup(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                  children: [
                    if (!_loadingSettings && _diaryEnabled)
                      lifeRow(Icons.book_outlined, l10n.diary, l10n.diaryHint,
                          () {
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
                    lifeRow(Icons.self_improvement_outlined, l10n.aiLife,
                        l10n.aiLifeHint, () {
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
                    lifeRow(Icons.auto_stories_outlined, l10n.memoryBook,
                        l10n.memoryBookHint, () {
                      Navigator.pop(sheetCtx);
                      _showMemoryBook();
                    }),
                    const IosCardDivider(),
                    lifeRow(Icons.timeline_outlined, l10n.timeline,
                        l10n.timelineHint, () {
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
                    lifeRow(Icons.inventory_2_outlined, l10n.chatArchive,
                        l10n.chatArchiveHint, () {
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
                    const IosCardDivider(),
                    lifeRow(Icons.psychology_alt_outlined, l10n.agentMind, '',
                        () {
                      Navigator.pop(sheetCtx);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => AgentMindScreen(
                            characterId: widget.character.id,
                            characterName: widget.character.name,
                          ),
                        ),
                      );
                    }),
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

  void _showMemoryBook() {
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

  // ── 日记 ──

  Future<List<String>> _fetchDiaryDates(int charId, String month) async {
    return _api.getDiaryDates(charId, month);
  }

  Future<String?> _fetchDiaryContent(int charId, String date) async {
    if (_diaryCache.containsKey(date)) return _diaryCache[date];
    try {
      final entries = await _api.getDiary(charId);
      for (final e in entries) {
        _diaryCache[e.diaryDate] = e.content;
      }
      return _diaryCache[date];
    } catch (_) {
      return null;
    }
  }

  // ── 记忆库入口 ──

  void _openMemoryEntry(String type) {
    final char = widget.character;
    switch (type) {
      case 'book':
        _showMemoryBook();
        break;
      case 'weave':
        // 织库入口：优先落在当前角色的织库页
        Navigator.push(
          context,
          AppPageRoute(
              builder: (_) =>
                  WeaveLibraryScreen(initialCharacterId: char.id)),
        );
        break;
      case 'timeline':
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => TimelineScreen(
              characterId: char.id,
              characterName: char.name,
            ),
          ),
        );
        break;
      case 'archive':
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => ArchiveScreen(
              characterName: char.name,
              sessionId: widget.sessionId,
            ),
          ),
        );
        break;
    }
  }

  // ── 头像 Widget ──

  Widget _buildAvatarImage(double size) {
    final char = widget.character;
    if (_avatarUrl != null && _avatarUrl!.isNotEmpty) {
      return Image.network(
        _api.resolveUrl(_avatarUrl!),
        width: size,
        height: size,
        fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => Container(
          color: Theme.of(context).colorScheme.secondaryContainer,
          child: Center(
            child: Text(
              char.name.isNotEmpty ? char.name[0] : '?',
              style: TextStyle(fontSize: size * 0.4),
            ),
          ),
        ),
      );
    }
    return Container(
      color: Theme.of(context).colorScheme.secondaryContainer,
      child: Center(
        child: Text(
          char.name.isNotEmpty ? char.name[0] : '?',
          style: TextStyle(fontSize: size * 0.4),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final char = widget.character;
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: const SizedBox.shrink(), // #10 名字只保留头像下方「名字+ID」
        actions: [
          // 手动发朋友圈（右上角独立）
          if (!_loadingSettings && _momentsEnabled)
            IconButton(
              icon: _publishingMoment
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.edit_note, size: 24),
              tooltip: l10n.manualMoment,
              onPressed: _publishingMoment ? null : _publishMoment,
            ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 32),
        children: [
          _buildAvatarArea(char, scheme, l10n),
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: CharacterCalendarFaces(
              diaryFace: DiaryCalendarFace(
                characterId: char.id,
                fetchDates: _fetchDiaryDates,
                fetchDiaryContent: _fetchDiaryContent,
              ),
              statusFace: _buildStatusFace(l10n, scheme),
              memoryFace: _buildMemoryFace(l10n, scheme),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildAvatarArea(
      AICharacter char, ColorScheme scheme, AppLocalizations l10n) {
    return Column(
      children: [
        // 头像 + 入口翻转轮播（与下方日历三面同款 PageView 透视翻转）
        CharacterEntryCarousel(
          height: 116,
          center: _buildAvatarCircle(scheme),
          entries: [
            // 第 1 个在头像左侧
            EntryCarouselItem(
              icon: Icons.settings,
              label: l10n.settingsTitle,
              onTap: _openSettings,
            ),
            // 其右依次：AI 内心世界、AI 生活
            EntryCarouselItem(
              icon: Icons.psychology_alt_outlined,
              label: l10n.agentMind,
              onTap: _openAgentMind,
            ),
            // 第三个入口：AI 生活（直达，不再弹角色生活 Sheet）
            EntryCarouselItem(
              icon: Icons.auto_awesome,
              label: l10n.aiLife, // 原为 l10n.charLife
              onTap: _openAiLife, // 原为 _showLifeSheet
            ),
          ],
        ),
        const SizedBox(height: 10),
        // 名字 + ID
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(char.name,
                style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    color: scheme.onSurface)),
            const SizedBox(width: 6),
            Text('#${char.id}',
                style: const TextStyle(
                    fontSize: 12, color: IosCardColors.subtitle)),
          ],
        ),
        const SizedBox(height: 6),
        // 操作提示
        Text(
          l10n.tapAvatarToChange,
          style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle),
        ),
      ],
    );
  }

  /// 头像圆（点击更换 / 长按气泡浮层）；水平滑动交给外层 [CharacterEntryCarousel]。
  Widget _buildAvatarCircle(ColorScheme scheme) {
    return GestureDetector(
      onTap: _uploadingAvatar ? null : _changeAvatar,
      onLongPress: _openBubbleOverlay,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Container(
            width: 96,
            height: 96,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              boxShadow: AppShadow.medium,
            ),
            child: ClipOval(child: _buildAvatarImage(96)),
          ),
          if (_uploadingAvatar)
            Positioned.fill(
              child: Container(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: Colors.black.withValues(alpha: 0.3),
                ),
                child: const Center(
                  child: SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
              ),
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
              child: Icon(Icons.camera_alt,
                  size: 14, color: scheme.onPrimary),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusFace(AppLocalizations l10n, ColorScheme scheme) {
    final s = _charState;
    final values = [
      s?.mood ?? 50,
      s?.bodyTemp ?? 50,
      s?.desire ?? 50,
      s?.possessiveness ?? 50,
      s?.fatigue ?? 50,
      s?.sensitivity ?? 50,
      s?.comfort ?? 50,
      s?.anger ?? 50,
    ];
    final labels = [
      l10n.mood,
      l10n.stateTemp,
      l10n.stateDesire,
      l10n.statePossessiveness,
      l10n.stateFatigue,
      l10n.stateSensitivity,
      l10n.stateComfort,
      l10n.stateAnger,
    ];
    final colors = [
      const Color(0xFFFFB74D), // 心情-橙
      const Color(0xFFEF5350), // 体温-红
      const Color(0xFFEC407A), // 欲望-粉
      const Color(0xFFAB47BC), // 占有-紫
      const Color(0xFF78909C), // 疲惫-灰蓝
      const Color(0xFF26C6DA), // 敏感-青
      const Color(0xFF66BB6A), // 舒适-绿
      const Color(0xFFFF7043), // 愤怒-深橙
    ];

    return StatusFace(
      values: values,
      labels: labels,
      colors: colors,
      statusText: widget.character.currentStatus ?? '…',
      onTap: () {
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => VisualStateScreen(
              characterId: widget.character.id,
              characterName: widget.character.name,
            ),
          ),
        );
      },
    );
  }

  Widget _buildMemoryFace(AppLocalizations l10n, ColorScheme scheme) {
    return MemoryFace(
      items: [
        MemoryBubbleItem(
          icon: Icons.auto_stories_outlined,
          label: l10n.memoryBook,
          onTap: () => _openMemoryEntry('book'),
        ),
        MemoryBubbleItem(
          icon: Icons.grid_view,
          label: l10n.weaveLibraryTitle,
          onTap: () => _openMemoryEntry('weave'),
        ),
        MemoryBubbleItem(
          icon: Icons.timeline_outlined,
          label: l10n.timeline,
          onTap: () => _openMemoryEntry('timeline'),
        ),
        MemoryBubbleItem(
          icon: Icons.inventory_2_outlined,
          label: l10n.chatArchive,
          onTap: () => _openMemoryEntry('archive'),
        ),
      ],
    );
  }
}
