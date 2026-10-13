// 游戏历史「人数」文案的 l10n 回归测试（P3，2026-10-13）。
//
// 旧形状：game_history_screen.dart 的副标题里写死了 `'$playerCount 人'`，
// 英文界面下也会出现中文量词。这里跑**真实生成的** AppLocalizations（zh/en 各一份），
// 断言数量词随 locale 走，并回源码证明列表页已经没有那个硬编码。
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

String _readProdSource(String relative) {
  for (final base in [relative, 'flutter_app/$relative']) {
    final f = File(base);
    if (f.existsSync()) return f.readAsStringSync(encoding: utf8);
  }
  throw StateError('找不到生产源码 $relative');
}

Future<AppLocalizations> _l10n(WidgetTester tester, String code) async {
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    locale: Locale(code),
    home: const SizedBox(),
  ));
  return AppLocalizations.of(tester.element(find.byType(SizedBox)))!;
}

void main() {
  testWidgets('zh：数量词用「人」，走真实生成的 l10n', (tester) async {
    final l10n = await _l10n(tester, 'zh');
    expect(l10n.gameHistoryPlayerCount(1), '1 人');
    expect(l10n.gameHistoryPlayerCount(6), '6 人');
    expect(l10n.gameHistoryPlayerCount(0), '0 人');
  });

  testWidgets('en：ICU 单复数（1 player / N players），不残留中文量词', (tester) async {
    final l10n = await _l10n(tester, 'en');
    expect(l10n.gameHistoryPlayerCount(1), '1 player');
    expect(l10n.gameHistoryPlayerCount(2), '2 players');
    expect(l10n.gameHistoryPlayerCount(6), '6 players');
    expect(l10n.gameHistoryPlayerCount(0), '0 players');
    for (final n in [0, 1, 2, 6]) {
      expect(l10n.gameHistoryPlayerCount(n), isNot(contains('人')));
    }
  });

  test('zh/en 成对存在同一 key（防单边漏加）', () {
    final zhArb = _readProdSource('lib/l10n/app_zh.arb');
    final enArb = _readProdSource('lib/l10n/app_en.arb');
    expect(zhArb, contains('"gameHistoryPlayerCount"'));
    expect(enArb, contains('"gameHistoryPlayerCount"'));
    // 两侧都必须声明 count 占位符，且占位符名一致
    expect(zhArb, contains('{count, plural'));
    expect(enArb, contains('{count, plural'));
  });

  test('列表页挂点：副标题改走 l10n，硬编码中文量词消失', () {
    final src = _readProdSource('lib/features/game/game_history_screen.dart');
    expect(src, contains(r'${l10n.gameHistoryPlayerCount(playerCount)}'));
    expect(src, isNot(contains(r"'$playerCount 人")));
  });
}
