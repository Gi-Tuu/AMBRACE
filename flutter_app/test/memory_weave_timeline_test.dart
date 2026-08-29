import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/screens/life/life_timeline_screen.dart';
import 'package:ai_companion/screens/memory/memory_book_screen.dart';
import 'package:ai_companion/screens/memory/timeline_screen.dart';
import 'package:ai_companion/screens/weave/weave_library_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P8 记忆本/织库/时光测试：
/// 玻璃 AppBar、AuroraCard 记忆卡、EmptyState 空态/错误态、时间线结构、生活时光行。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    _mockAll(api);
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

  group('MemoryBookScreen', () {
    testWidgets('glass AppBar + AuroraCard memory card with stars/source',
        (tester) async {
      await tester.pumpWidget(host(
          const MemoryBookScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Alpha'), findsWidgets); // AppBar 标题
      expect(find.textContaining('remembered content'), findsOneWidget);
      expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
      expect(find.byIcon(Icons.star), findsWidgets); // 星标
    });

    testWidgets('error -> EmptyState loadFailed + retry recovers',
        (tester) async {
      api.json('GET', '/api/v1/memories', {'detail': 'boom'}, status: 500);
      await tester.pumpWidget(host(
          const MemoryBookScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(MemoryBookScreen)))!;

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.loadFailed), findsOneWidget);
      expect(find.text(l10n.retry), findsOneWidget);

      _mockAll(api);
      await tester.tap(find.text(l10n.retry));
      await tester.pumpAndSettle();
      expect(find.byType(EmptyState), findsNothing);
    });

    testWidgets('empty -> EmptyState with noMemories', (tester) async {
      api.json('GET', '/api/v1/memories', {
        'memories': [],
        'total': 0,
      });
      await tester.pumpWidget(host(
          const MemoryBookScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(MemoryBookScreen)))!;

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noMemories), findsOneWidget);
    });
  });

  group('WeaveLibraryScreen', () {
    testWidgets('glass AppBar + AuroraCard list tiles render', (tester) async {
      await tester.pumpWidget(host(const WeaveLibraryScreen()));
      await tester.pumpAndSettle();

      expect(find.textContaining('weave card title'), findsOneWidget);
      expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
    });

    testWidgets('detail opens FloatingSheet with content', (tester) async {
      await tester.pumpWidget(host(const WeaveLibraryScreen()));
      await tester.pumpAndSettle();

      await tester.tap(find.textContaining('weave card title'));
      await tester.pumpAndSettle();

      // FloatingSheet 拖拽条 + 详情内容（详情接口返回完整卡）
      expect(find.byKey(const Key('floatingSheetHandle')), findsOneWidget);
      expect(find.textContaining('weave card title'), findsWidgets);
    });
  });

  group('TimelineScreen', () {
    testWidgets('glass AppBar + days card + timeline entries in AuroraCard',
        (tester) async {
      await tester.pumpWidget(host(
          const TimelineScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(TimelineScreen)))!;

      expect(find.text(l10n.daysKnown('Alpha', 300)), findsOneWidget);
      expect(find.text('first meeting milestone'), findsOneWidget);
      expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
    });
  });

  group('LifeTimelineScreen', () {
    testWidgets('glass AppBar + AuroraCard rows render', (tester) async {
      await tester.pumpWidget(host(
          const LifeTimelineScreen(characterId: 1, characterName: 'Alpha')));
      await tester.pumpAndSettle();

      expect(find.textContaining('life moment content'), findsOneWidget);
      expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
    });
  });
}

void _mockAll(FakeApiAdapter api) {
  api.json('GET', '/api/v1/memories', {
    'memories': [
      {
        'id': 1,
        'character_id': 1,
        'memory_type': 'event',
        'content': 'remembered content',
        'importance': 4,
        'speaker_type': 'user',
        'is_locked': false,
      },
    ],
    'total': 1,
  });
  api.json('GET', '/api/v1/weave/cards', {
    'cards': [
      {
        'id': 1,
        'character_id': 1,
        'title': 'weave card title',
        'summary': 'weave card summary text',
        'memory_count': 3,
        'created_at': '2026-08-28T02:00:00Z',
      },
    ],
    'total': 1,
  });
  api.json('GET', '/api/v1/weave/cards/1', {
    'id': 1,
    'character_id': 1,
    'title': 'weave card title',
    'summary': 'weave card summary text',
    'memory_count': 3,
    'created_at': '2026-08-28T02:00:00Z',
  });
  api.json('GET', '/api/v1/timeline/1', {
    'character_id': 1,
    'character_name': 'Alpha',
    'days_known': 300,
    'has_milestones': true,
    'items': [
      {
        'date': '2026-08-28',
        'type': 'first_chat',
        'title': 'first meeting milestone',
        'desc': 'the day we first talked',
      },
    ],
  });
  api.json('GET', '/api/v1/life/timeline', {
    'items': [
      {
        'id': 1,
        'sub_type': 'life_event',
        'content': 'life moment content',
        'created_at': '2026-08-28T02:00:00Z',
      },
    ],
  });
}
