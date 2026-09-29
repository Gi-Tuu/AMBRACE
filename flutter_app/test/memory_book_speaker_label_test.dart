import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/features/memory/memory_book_screen.dart';

/// 记忆本「归属标注」兜底回归（Z6）：
/// speaker_type 表驱动映射，未知/脏值走中性标签，绝不回显原始枚举。
Future<AppLocalizations> _loadL10n(WidgetTester tester, String lang) async {
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    locale: Locale(lang),
    home: const SizedBox(),
  ));
  return AppLocalizations.of(tester.element(find.byType(SizedBox)))!;
}

void main() {
  testWidgets('zh：四个已知归属各自出标签', (tester) async {
    final l10n = await _loadL10n(tester, 'zh');
    expect(memorySpeakerLabel(l10n, 'user'), '用户');
    expect(memorySpeakerLabel(l10n, 'character'), '角色');
    expect(memorySpeakerLabel(l10n, 'system'), '系统');
    expect(memorySpeakerLabel(l10n, 'perception'), '感知');
  });

  testWidgets('zh：未知值兜底中性标签，不回显原始枚举', (tester) async {
    final l10n = await _loadL10n(tester, 'zh');
    for (final dirty in ['ai', 'bot', 'perceptions', 'SYSTEM2', 'unknown']) {
      final label = memorySpeakerLabel(l10n, dirty)!;
      expect(label, '来源未标注');
      expect(label.toLowerCase(), isNot(dirty.toLowerCase()));
    }
  });

  testWidgets('空值/空白不出标签且不抛错', (tester) async {
    final l10n = await _loadL10n(tester, 'zh');
    expect(memorySpeakerLabel(l10n, null), isNull);
    expect(memorySpeakerLabel(l10n, ''), isNull);
    expect(memorySpeakerLabel(l10n, '   '), isNull);
  });

  testWidgets('大小写/首尾空格归一后仍命中已知值', (tester) async {
    final l10n = await _loadL10n(tester, 'zh');
    expect(memorySpeakerLabel(l10n, ' User '), '用户');
    expect(memorySpeakerLabel(l10n, 'Perception'), '感知');
  });

  testWidgets('en：同一张表，标签齐备且无中文残留', (tester) async {
    final l10n = await _loadL10n(tester, 'en');
    expect(memorySpeakerLabel(l10n, 'user'), 'User');
    expect(memorySpeakerLabel(l10n, 'character'), 'Character');
    expect(memorySpeakerLabel(l10n, 'system'), 'System');
    expect(memorySpeakerLabel(l10n, 'perception'), 'Perception');
    expect(memorySpeakerLabel(l10n, 'weird'), 'Source not labeled');
  });
}
