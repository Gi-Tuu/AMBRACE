import 'dart:io';

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../theme/app_theme.dart';
import '../../theme/skins/skin.dart';
import '../../theme/skins/skin_registry.dart';
import '../../widgets/ios_card_group.dart';

/// 外观页：皮肤 + 主题模式（跟随系统/浅色/深色）+ 主题色（6 款）+ 语言。
class AppearanceScreen extends StatelessWidget {
  const AppearanceScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final l10n = AppLocalizations.of(context)!;
    final brightness = Theme.of(context).brightness;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appearanceTitle)),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          // ── 皮肤选择 ──
          IosCardGroup(
            title: l10n.skinTitle,
            children: [
              SizedBox(
                height: 132,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  itemCount: SkinRegistry.all.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 14),
                  itemBuilder: (context, index) {
                    final skin = SkinRegistry.all[index];
                    final selected = settings.skinId == skin.id;
                    return _SkinOption(
                      skin: skin,
                      name: skinName(l10n, skin),
                      brightness: brightness,
                      seedColor: AppTheme.seedColorAt(settings.seedColorIndex),
                      selected: selected,
                      onTap: () => settings.setSkinId(skin.id),
                    );
                  },
                ),
              ),
            ],
          ),
          if (settings.skinId == 'glass') const _GlassBackgroundSection(),
          IosCardGroup(
            title: l10n.themeMode,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: SegmentedButton<int>(
                  segments: [
                    ButtonSegment(value: 0, label: Text(l10n.followSystem), icon: const Icon(Icons.brightness_auto)),
                    ButtonSegment(value: 1, label: Text(l10n.light), icon: const Icon(Icons.light_mode_outlined)),
                    ButtonSegment(value: 2, label: Text(l10n.dark), icon: const Icon(Icons.dark_mode_outlined)),
                  ],
                  selected: {settings.themeModeIndex},
                  onSelectionChanged: (sel) => settings.setThemeModeIndex(sel.first),
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.themeColor,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  children: [
                    for (var i = 0; i < AppTheme.seedColors.length; i++)
                      _ColorOption(
                        color: AppTheme.seedColors[i],
                        name: _seedName(l10n, i),
                        selected: settings.seedColorIndex == i,
                        onTap: () => settings.setSeedColorIndex(i),
                      ),
                  ],
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.language,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: SegmentedButton<String>(
                  segments: [
                    ButtonSegment(value: 'system', label: Text(l10n.followSystem), icon: const Icon(Icons.language)),
                    ButtonSegment(value: 'zh', label: Text(l10n.simplifiedChinese)),
                    ButtonSegment(value: 'en', label: Text(l10n.english)),
                  ],
                  selected: {settings.localeCode},
                  onSelectionChanged: (sel) => settings.setLocale(sel.first),
                ),
              ),
            ],
          ),
          // ── 性能与动效（全局「减少动效 / 降低模糊」开关，Phase 1 D2） ──
          IosCardGroup(
            title: l10n.performanceMotionTitle,
            children: [
              SwitchListTile(
                title: Text(l10n.reduceMotionLabel),
                subtitle: Text(l10n.reduceMotionHint),
                value: settings.reduceMotion,
                onChanged: (v) => settings.setReduceMotion(v),
              ),
              SwitchListTile(
                title: Text(l10n.reduceBlurLabel),
                subtitle: Text(l10n.reduceBlurHint),
                value: settings.reduceBlur,
                onChanged: (v) => settings.setReduceBlur(v),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 20, top: 4, bottom: 16),
            child: Text(
              l10n.currentPreview(_modeName(l10n, settings.themeModeIndex), _seedName(l10n, settings.seedColorIndex)),
              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
            ),
          ),
        ],
      ),
    );
  }

  String _seedName(AppLocalizations l10n, int i) {
    switch (i) {
      case 0:
        return l10n.themeColorBlue;
      case 1:
        return l10n.themeColorPurple;
      case 2:
        return l10n.themeColorPink;
      case 3:
        return l10n.themeColorCyan;
      case 4:
        return l10n.themeColorGreen;
      default:
        return l10n.themeColorOrange;
    }
  }

  String _modeName(AppLocalizations l10n, int index) {
    switch (index) {
      case 1:
        return l10n.light;
      case 2:
        return l10n.dark;
      default:
        return l10n.followSystem;
    }
  }

  /// 皮肤名 l10n 解析（按 skin.id 映射；未知 id 回退到 displayName）
  static String skinName(AppLocalizations l10n, Skin skin) {
    switch (skin.id) {
      case 'ios':
        return l10n.skinNameIos;
      case 'warm':
        return l10n.skinNameWarm;
      case 'material':
        return l10n.skinNameMaterial;
      case 'paper':
        return l10n.skinNamePaper;
      case 'neon':
        return l10n.skinNameNeon;
      case 'glass':
        return l10n.skinNameGlass;
      default:
        return skin.displayName;
    }
  }
}

/// 毛玻璃背景设置分区（仅 glass 皮肤显示）：来源 / 模糊 / 压暗 / 渐变配色 / 重置。
class _GlassBackgroundSection extends StatefulWidget {
  const _GlassBackgroundSection();

  @override
  State<_GlassBackgroundSection> createState() => _GlassBackgroundSectionState();
}

class _GlassBackgroundSectionState extends State<_GlassBackgroundSection> {
  bool _pickingImage = false;

  Future<void> _pickImage(SettingsProvider settings) async {
    if (_pickingImage) return;
    _pickingImage = true;
    final l10n = AppLocalizations.of(context)!;
    try {
      final picked = await ImagePicker()
          .pickImage(source: ImageSource.gallery, maxWidth: 1920, imageQuality: 90);
      if (picked == null) return; // 用户取消
      final docs = await getApplicationDocumentsDirectory();
      final dir = Directory('${docs.path}/backgrounds');
      await dir.create(recursive: true);
      final dest = File('${dir.path}/custom.jpg');
      // 覆盖旧文件
      await File(picked.path).copy(dest.path);
      await settings.setGlassBackgroundPath(dest.path);
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.glassImageSaved)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.glassImagePickFailed)));
      }
    } finally {
      _pickingImage = false;
    }
  }

  Future<void> _pickAuroraColor(SettingsProvider settings, {required bool isStart}) async {
    final l10n = AppLocalizations.of(context)!;
    final initial = isStart
        ? (settings.glassAuroraColor1 != null
            ? Color(settings.glassAuroraColor1!)
            : AppTheme.seedColorAt(settings.seedColorIndex))
        : (settings.glassAuroraColor2 != null
            ? Color(settings.glassAuroraColor2!)
            : AppTheme.seedColorAt(settings.seedColorIndex));
    final picked = await _showColorPicker(
      isStart ? l10n.glassAuroraStart : l10n.glassAuroraEnd,
      initial,
    );
    if (picked == null) return;
    if (isStart) {
      await settings.setGlassAuroraColors(c1: picked);
    } else {
      await settings.setGlassAuroraColors(c2: picked);
    }
  }

  Future<Color?> _showColorPicker(String title, Color initial) {
    return showDialog<Color>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final color in Colors.primaries)
              InkWell(
                borderRadius: BorderRadius.circular(22),
                onTap: () => Navigator.pop(context, color.shade500),
                child: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: color.shade500,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: color.shade500 == initial ? Colors.black : Colors.transparent,
                      width: 3,
                    ),
                  ),
                ),
              ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text(title)),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final l10n = AppLocalizations.of(context)!;
    final hasImage = settings.glassBackgroundPath != null && settings.glassBackgroundPath!.isNotEmpty;
    final seedColor = AppTheme.seedColorAt(settings.seedColorIndex);
    final aurora1 = settings.glassAuroraColor1 != null ? Color(settings.glassAuroraColor1!) : seedColor;
    final aurora2 = settings.glassAuroraColor2 != null ? Color(settings.glassAuroraColor2!) : seedColor;

    return IosCardGroup(
      title: l10n.glassBackgroundTitle,
      children: [
        // 来源选择
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Center(
            child: SegmentedButton<String>(
              segments: [
                ButtonSegment(value: 'gradient', label: Text(l10n.glassBackgroundGradient)),
                ButtonSegment(value: 'image', label: Text(l10n.glassBackgroundImage)),
              ],
              selected: {hasImage ? 'image' : 'gradient'},
              onSelectionChanged: (sel) {
                if (sel.first == 'image') {
                  _pickImage(settings);
                } else {
                  settings.setGlassBackgroundPath(null);
                }
              },
            ),
          ),
        ),
        // 模糊滑杆
        _sliderRow(
          label: l10n.glassBlurLabel,
          value: settings.glassBlur,
          min: 0,
          max: 30,
          divisions: 30,
          display: settings.glassBlur.round().toString(),
          onChanged: (v) => settings.setGlassBlur(v),
        ),
        // 压暗滑杆
        _sliderRow(
          label: l10n.glassDimLabel,
          value: settings.glassDim,
          min: 0,
          max: 0.6,
          divisions: 60,
          display: settings.glassDim.toStringAsFixed(1),
          onChanged: (v) => settings.setGlassDim(v),
          hint: l10n.glassDimHint,
        ),
        // 渐变配色
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Row(
            children: [
              _colorDot(l10n.glassAuroraStart, aurora1, () => _pickAuroraColor(settings, isStart: true)),
              const SizedBox(width: 16),
              _colorDot(l10n.glassAuroraEnd, aurora2, () => _pickAuroraColor(settings, isStart: false)),
            ],
          ),
        ),
        // 选择图片 / 重置
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Row(
            children: [
              OutlinedButton.icon(
                onPressed: _pickingImage ? null : () => _pickImage(settings),
                icon: const Icon(Icons.image_outlined, size: 18),
                label: Text(l10n.glassPickImage),
              ),
              const Spacer(),
              TextButton(
                onPressed: () => settings.resetGlassBackground(),
                child: Text(l10n.glassBackgroundReset),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _sliderRow({
    required String label,
    required double value,
    required double min,
    required double max,
    required int divisions,
    required String display,
    required ValueChanged<double> onChanged,
    String? hint,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              SizedBox(width: 44, child: Text(label, style: const TextStyle(fontSize: 13))),
              Expanded(
                child: Slider(
                  value: value,
                  min: min,
                  max: max,
                  divisions: divisions,
                  label: display,
                  onChanged: onChanged,
                ),
              ),
              SizedBox(
                width: 32,
                child: Text(display, textAlign: TextAlign.right, style: const TextStyle(fontSize: 13)),
              ),
            ],
          ),
          if (hint != null)
            Padding(
              padding: const EdgeInsets.only(left: 44),
              child: Text(hint, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
            ),
        ],
      ),
    );
  }

  Widget _colorDot(String label, Color color, VoidCallback onTap) {
    return Expanded(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Row(
          children: [
            Container(
              width: 36,
              height: 36,
              decoration: BoxDecoration(
                color: color,
                shape: BoxShape.circle,
                border: Border.all(color: Colors.black26),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(label, style: const TextStyle(fontSize: 13), overflow: TextOverflow.ellipsis),
            ),
          ],
        ),
      ),
    );
  }
}

/// 皮肤选项卡：迷你聊天气泡预览 + 名称 + 选中勾
class _SkinOption extends StatelessWidget {
  final Skin skin;
  final String name;
  final Brightness brightness;
  final Color seedColor;
  final bool selected;
  final VoidCallback onTap;
  const _SkinOption({
    required this.skin,
    required this.name,
    required this.brightness,
    required this.seedColor,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    // 不支持深色的皮肤在深色下用浅色预览（实际运行时也会回退）
    final effectiveBrightness =
        (brightness == Brightness.dark && !skin.supportsDarkMode) ? Brightness.light : brightness;
    final themeData = skin.buildThemeData(brightness: effectiveBrightness, seedColor: seedColor);
    final colors = skin.buildSkinColors(brightness: effectiveBrightness, seedColor: seedColor);
    final previewBg = themeData.scaffoldBackgroundColor;
    final userBubble = colors.bubbleUser ?? themeData.colorScheme.primaryContainer;
    final aiBubble = colors.bubbleAi ?? themeData.colorScheme.surfaceContainerHighest;
    final userText = colors.bubbleUserText ?? themeData.colorScheme.onPrimaryContainer;
    final aiText = colors.bubbleAiText ?? themeData.colorScheme.onSurfaceVariant;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        width: 104,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: previewBg,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: selected ? Theme.of(context).colorScheme.primary : Theme.of(context).dividerColor.withValues(alpha: 0.5),
            width: selected ? 2.5 : 1,
          ),
          boxShadow: selected
              ? [BoxShadow(color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.18), blurRadius: 8, offset: const Offset(0, 2))]
              : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 迷你聊天预览
            Expanded(
              child: Stack(
                children: [
                  // AI 气泡（左）
                  Positioned(
                    top: 6,
                    left: 0,
                    child: _miniBubble(aiBubble, aiText, 38),
                  ),
                  // 用户气泡（右）
                  Positioned(
                    bottom: 6,
                    right: 0,
                    child: _miniBubble(userBubble, userText, 30),
                  ),
                  if (selected)
                    Positioned(
                      top: 0,
                      right: 0,
                      child: Container(
                        padding: const EdgeInsets.all(2),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.primary,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.check, color: Colors.white, size: 13),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 6),
            Text(
              name,
              style: TextStyle(
                fontSize: 12,
                fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                color: aiText,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }

  Widget _miniBubble(Color bg, Color fg, double width) {
    return Container(
      width: width,
      height: 16,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
      ),
      alignment: Alignment.center,
      child: Container(
        width: width * 0.55,
        height: 4,
        decoration: BoxDecoration(
          color: fg.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}

class _ColorOption extends StatelessWidget {
  final Color color;
  final String name;
  final bool selected;
  final VoidCallback onTap;
  const _ColorOption({required this.color, required this.name, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
              border: Border.all(
                color: selected ? Theme.of(context).colorScheme.onSurface : Colors.transparent,
                width: 3,
              ),
            ),
            child: selected
                ? const Icon(Icons.check, color: Colors.white, size: 22)
                : null,
          ),
          const SizedBox(height: 4),
          Text(name, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }
}
