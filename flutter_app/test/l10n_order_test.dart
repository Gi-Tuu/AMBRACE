import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 多占位符 l10n 参数顺序回归测试：
/// gen-l10n 对无 placeholders 元数据的 key 会按字母序生成参数，
/// 与调用点模板顺序错位会产出乱序文本（历史 bug：朋友圈时间显示错乱）。
void main() {
  Widget wrap(Widget child) => MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        locale: const Locale('zh'),
        home: child,
      );

  testWidgets('momentDateFull 顺序 = 年月日时间', (tester) async {
    await tester.pumpWidget(wrap(const SizedBox()));
    final l10n = AppLocalizations.of(tester.element(find.byType(SizedBox)))!;
    expect(l10n.momentDateFull(2026, 8, 16, '16:08'), '2026年8月16日 16:08');
    expect(l10n.momentDateFull(2026, 8, 16, '16:08'), isNot('16:08年8月2026月日16'));
  });

  testWidgets('dateFull/dateMonthDay/dayLabel 顺序', (tester) async {
    await tester.pumpWidget(wrap(const SizedBox()));
    final l10n = AppLocalizations.of(tester.element(find.byType(SizedBox)))!;
    expect(l10n.dateFull(2026, 8, 16), '2026年8月16日');
    expect(l10n.dateMonthDay(8, 16), '8月16日');
    expect(l10n.dayLabel(8, 16), '8月16日');
    expect(l10n.daysKnown('小遥', 300), '认识 小遥 第 300 天');
  });

  testWidgets('dndOn/likersTextMany/currentPreview/calendarTitle 顺序', (tester) async {
    await tester.pumpWidget(wrap(const SizedBox()));
    final l10n = AppLocalizations.of(tester.element(find.byType(SizedBox)))!;
    expect(l10n.dndOn('22:00', '07:00'), '22:00 - 07:00 不发送主动消息');
    expect(l10n.likersTextMany('A、B', 2), 'A、B 等 2 人觉得很赞');
    expect(l10n.calendarTitle(2026, 8), '2026年8月');
    expect(l10n.currentPreview('深色', '蓝色'), '当前：深色 · 蓝色');
    expect(l10n.charPetTitle('小遥', '球球'), '小遥 的 球球');
  });
}