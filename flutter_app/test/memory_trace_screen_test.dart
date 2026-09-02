import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/character/memory_trace_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// #70-B 记忆检索轨迹只读面板测试：
/// 玻璃 AppBar + 轨迹卡摘要行 + 点击展开 steps（dense/sparse/rrf/rerank/returned）、空态、错误态恢复。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    _mockTrace(api);
  });

  Widget app() => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: const MemoryTraceScreen(characterId: 1, characterName: 'Alpha'),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(MemoryTraceScreen)))!;

  testWidgets('renders AppBar + trace summary + expandable steps', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    // AppBar 标题
    expect(find.text(l10n.memoryTraceTitle), findsOneWidget);
    // 摘要行：query 显示 + route/候选/延迟
    expect(find.text('最近聊了什么'), findsOneWidget);
    expect(find.textContaining('hybrid'), findsOneWidget);
    expect(find.textContaining('42 ms'), findsOneWidget);

    // 未展开前 steps 标签不可见
    expect(find.text(l10n.memoryTraceDense), findsNothing);

    // 点击展开 → steps 分区出现（dense/sparse/rrf/rerank/returned）
    await tester.tap(find.text('最近聊了什么'));
    await tester.pumpAndSettle();
    expect(find.text(l10n.memoryTraceDense), findsOneWidget);
    expect(find.text(l10n.memoryTraceSparse), findsOneWidget);
    expect(find.text(l10n.memoryTraceRrf), findsOneWidget);
    expect(find.text(l10n.memoryTraceRerank), findsOneWidget);
    expect(find.text(l10n.memoryTraceReturned), findsOneWidget);
    expect(find.textContaining('用户喜欢画画'), findsOneWidget);
    expect(find.textContaining('用户下周去北京'), findsOneWidget);
  });

  testWidgets('empty traces shows empty text', (tester) async {
    api.json('GET', '/api/v1/characters/1/memory-trace', {
      'character_id': 1,
      'traces': <Object?>[],
    });
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.memoryTraceEmpty), findsOneWidget);
  });

  testWidgets('error state -> loadFailed + retry recovers', (tester) async {
    api.json('GET', '/api/v1/characters/1/memory-trace', {'detail': 'boom'},
        status: 500);
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.byType(EmptyState), findsOneWidget);
    expect(find.text(l10n.loadFailed), findsOneWidget);
    expect(find.text(l10n.retry), findsOneWidget);

    _mockTrace(api);
    await tester.tap(find.text(l10n.retry));
    await tester.pumpAndSettle();
    expect(find.byType(EmptyState), findsNothing);
    expect(find.text('最近聊了什么'), findsOneWidget);
  });
}

Map<String, Object?> _traceData() => {
      'character_id': 1,
      'traces': [
        {
          'id': 1,
          'route': 'hybrid',
          'latency_ms': 42,
          'status': 'ok',
          'created_at': '2026-08-28T02:00:00Z',
          'steps': {
            'query': '最近聊了什么',
            'derived_queries': ['话题'],
            'dense_hits': [10, 11],
            'sparse_hits': [12],
            'rrf_top': [11, 10, 12],
            'candidate_count': 3,
            'hit_count': 3,
            'returned': [
              {'id': 11, 'preview': '用户喜欢画画'},
              {'id': 10, 'preview': '用户下周去北京'},
            ],
            'latency_ms': 42,
            'db_pool': 3,
            'rerank_top': [
              {'id': 11, 'score': 0.9, 'importance': 60.0, 'has_why': true, 'status': 'active'},
            ],
            'route': 'hybrid',
          },
        },
      ],
    };

void _mockTrace(FakeApiAdapter api) {
  api.json('GET', '/api/v1/characters/1/memory-trace', _traceData());
}
