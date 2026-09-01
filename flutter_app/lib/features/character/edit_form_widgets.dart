// 深拆（F7-c-7，2026-09-01）自 screens/character/character_edit_screen.dart 迁入；
// 编辑表单通用构件（纯展示，控制器/值经参数传入，变更经回调上抛），逻辑逐字节保持。
import 'package:flutter/material.dart';

import 'package:ai_companion/l10n/app_localizations.dart';

import '../../widgets/aurora_card.dart';
import '../../widgets/ios_card_group.dart';

/// Aurora P5 分组（与 settings_tiles.auroraGroup 同构；编辑表单独立引用）
Widget editAuroraGroup({required String title, required List<Widget> children}) {
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

/// Aurora P5 滑杆统一主题（active 轨道 + 主题色滑块）
Widget themedSlider(BuildContext context, Widget slider) {
  final scheme = Theme.of(context).colorScheme;
  return SliderTheme(
    data: SliderThemeData(
      activeTrackColor: scheme.primary,
      inactiveTrackColor: scheme.primary.withValues(alpha: 0.2),
      thumbColor: scheme.primary,
      overlayColor: scheme.primary.withValues(alpha: 0.12),
      trackHeight: 3,
    ),
    child: slider,
  );
}

/// 文本输入行（controller 由屏幕持有并 dispose）
class EditField extends StatelessWidget {
  final TextEditingController ctrl;
  final String label;
  final String? hint;
  final int maxLines;
  final TextInputType? keyboard;
  final String? Function(String?)? validator;
  final bool compact;

  const EditField({
    super.key,
    required this.ctrl,
    required this.label,
    this.hint,
    this.maxLines = 1,
    this.keyboard,
    this.validator,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
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
          hintStyle: TextStyle(color: scheme.onSurfaceVariant),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide.none,
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide:
                BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.4)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: scheme.primary, width: 1.5),
          ),
          filled: true,
          fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        ),
      ),
    );
  }
}

/// 下拉选择行
class EditDropdown extends StatelessWidget {
  final String label;
  final String? value;
  final List<DropdownMenuItem<String>> items;
  final ValueChanged<String?> onChanged;
  final String? helper;

  const EditDropdown({
    super.key,
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.helper,
  });

  @override
  Widget build(BuildContext context) {
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
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide.none,
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide:
                BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.4)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: scheme.primary, width: 1.5),
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
}

/// 语速滑杆（0.5x-1.5x）
class VoiceRateSlider extends StatelessWidget {
  final double value;
  final ValueChanged<double> onChanged;

  const VoiceRateSlider({super.key, required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
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
              Text('${value.toStringAsFixed(1)}x',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          themedSlider(
            context,
            Slider(
              value: value.clamp(0.5, 1.5).toDouble(),
              min: 0.5,
              max: 1.5,
              divisions: 10,
              label: '${value.toStringAsFixed(1)}x',
              onChanged: onChanged,
            ),
          ),
        ],
      ),
    );
  }
}

/// 音调滑杆（-20~+20 Hz，0=正常）
class VoicePitchSlider extends StatelessWidget {
  final double value;
  final ValueChanged<double> onChanged;

  const VoicePitchSlider({super.key, required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final hz = value.toStringAsFixed(0);
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, top: 8, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.voicePitch, style: const TextStyle(fontSize: 15)),
              Text(value == 0 ? l10n.pitchNormal : '${value > 0 ? '+' : ''}$hz Hz',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          themedSlider(
            context,
            Slider(
              value: value.clamp(-20, 20).toDouble(),
              min: -20,
              max: 20,
              divisions: 8,
              label: '$hz Hz',
              onChanged: onChanged,
            ),
          ),
        ],
      ),
    );
  }
}

/// 话痨度区段：滑杆（0-100，设置后置 _talkativenessSet）+ 锁定开关
class TalkativenessSection extends StatelessWidget {
  final double value;
  final ValueChanged<double> onChanged;
  final bool locked;
  final ValueChanged<bool> onLockedChanged;

  const TalkativenessSection({
    super.key,
    required this.value,
    required this.onChanged,
    required this.locked,
    required this.onLockedChanged,
  });

  @override
  Widget build(BuildContext context) {
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
              Text('${value.round()}',
                  style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)),
            ],
          ),
          themedSlider(
            context,
            Slider(
              value: value.clamp(0, 100).toDouble(),
              min: 0,
              max: 100,
              divisions: 100,
              label: '${value.round()}',
              onChanged: onChanged,
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            dense: true,
            title: Text(l10n.talkativenessLocked,
                style: const TextStyle(fontSize: 13)),
            subtitle: Text(l10n.talkativenessLockedHint,
                style: TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
            value: locked,
            onChanged: onLockedChanged,
          ),
        ],
      ),
    );
  }
}
