import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/screens/diary/diary_screen.dart';
import 'package:ai_companion/screens/diary/my_diary_edit_screen.dart';
import 'package:ai_companion/screens/diary/my_diary_screen.dart';
import 'package:ai_companion/screens/diary/my_memos_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P7 日记四页测试：
/// 角色日记（玻璃 AppBar + AuroraCard 条目 + 空态）、我的日记（列表/FAB/空态）、
/// 日记编辑（分组/输入框）、我的备忘（列表/空态）。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    final today =
        '${DateTime.now().year.toString().padLeft(4, '0')}-${DateTime.now().month.toString().padLeft(2, '0')}-${DateTime.now().day.toString().padLeft(2, '0')}';
    api.json('GET', '/api/v1/diary/1', {
      'entries': [
        {
          'id': 1,
          'diary_date': today, // 日记树只自动展开今天
          'content': 'diary content for today',
          'character_id': 1,
        },
      ],
    });
    api.json('GET', '/api/v1/privacy/1/diary', {'enabled': false});
    api.json('GET', '/api/v1/user/diaries', {
      'diaries': [
        {
          'id': 1,
          'diary_date': '2026-08-28',
          'content': 'my own diary entry',
        },
      ],
    });
    api.json('GET', '/api/v1/user/memos', {
      'memos': [
        {
          'id': 1,
          'title': 'shopping list',
          'content': 'milk and eggs',
        },
      ],
    });
  });

  Widget host(Widget child) => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: child,
      );

  group('DiaryScreen (character diary)', () {
    testWidgets('AppBar + AuroraCard diary entry render', (tester) async {
      await tester.pumpWidget(host(
          const DiaryScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();

      expect(find.text('Alpha的日记'), findsOneWidget);
      // 修复后：今天分区自动展开，条目无需手动点击即可见。
      expect(find.textContaining('diary content for today'), findsOneWidget);
      expect(find.byType(AuroraCard), findsOneWidget);
    });

    testWidgets(
        'today group auto-expands, MM-dd label, collapse/expand works',
        (tester) async {
      final todayLabel =
          '${DateTime.now().month.toString().padLeft(2, '0')}-${DateTime.now().day.toString().padLeft(2, '0')}';
      await tester.pumpWidget(host(
          const DiaryScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();

      // 今天分区自动展开：条目无需点击即可见。
      expect(find.textContaining('diary content for today'), findsOneWidget);
      expect(find.byType(AuroraCard), findsOneWidget);

      // 日期标签为 MM-dd，绝不出现重复拼接形如「2026-08-2026-08-29」。
      expect(find.textContaining('2026-08-2026-08-29'), findsNothing);
      expect(find.text(todayLabel), findsWidgets);

      // 点击收起：条目隐藏。
      await tester.tap(find.widgetWithText(InkWell, todayLabel));
      await tester.pumpAndSettle();
      expect(find.textContaining('diary content for today'), findsNothing);

      // 点击展开：条目重新可见。
      await tester.tap(find.widgetWithText(InkWell, todayLabel));
      await tester.pumpAndSettle();
      expect(find.textContaining('diary content for today'), findsOneWidget);
    });

    testWidgets('empty -> EmptyState with noDiary', (tester) async {
      api.json('GET', '/api/v1/diary/1', {'entries': []});
      await tester.pumpWidget(host(
          const DiaryScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(DiaryScreen)))!;

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noDiary), findsOneWidget);
    });
  });

  group('MyDiaryScreen', () {
    testWidgets('list renders + FAB exists', (tester) async {
      await tester.pumpWidget(host(const MyDiaryScreen()));
      await tester.pumpAndSettle();

      expect(find.text('2026-08-28'), findsOneWidget);
      expect(find.textContaining('my own diary entry'), findsOneWidget);
      expect(find.byType(AuroraCard), findsOneWidget);
      expect(find.byType(FloatingActionButton), findsOneWidget);
    });

    testWidgets('empty -> EmptyState with noDiaryHint', (tester) async {
      api.json('GET', '/api/v1/user/diaries', {'diaries': []});
      await tester.pumpWidget(host(const MyDiaryScreen()));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(tester.element(find.byType(MyDiaryScreen)))!;

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noDiaryHint), findsOneWidget);
    });
  });

  group('MyDiaryEditScreen', () {
    testWidgets('glass AppBar + aurora groups + themed inputs render',
        (tester) async {
      await tester.pumpWidget(host(const MyDiaryEditScreen()));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(MyDiaryEditScreen)))!;

      expect(find.text(l10n.editDiary), findsOneWidget);
      expect(find.text(l10n.date), findsWidgets);
      expect(find.byType(AuroraCard), findsNWidgets(2));
      expect(find.byType(TextField), findsNWidgets(2));
    });
  });

  group('MyMemosScreen', () {
    testWidgets('list renders + FAB exists', (tester) async {
      await tester.pumpWidget(host(const MyMemosScreen()));
      await tester.pumpAndSettle();

      expect(find.text('shopping list'), findsOneWidget);
      expect(find.textContaining('milk and eggs'), findsOneWidget);
      expect(find.byType(AuroraCard), findsOneWidget);
      expect(find.byType(FloatingActionButton), findsOneWidget);
    });

    testWidgets('empty -> EmptyState with noMemosHint', (tester) async {
      api.json('GET', '/api/v1/user/memos', {'memos': []});
      await tester.pumpWidget(host(const MyMemosScreen()));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(tester.element(find.byType(MyMemosScreen)))!;

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noMemosHint), findsOneWidget);
    });
  });
}
