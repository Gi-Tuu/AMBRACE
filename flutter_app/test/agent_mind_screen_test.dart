import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/character/agent_mind_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P6 AI 内心世界测试：
/// 玻璃 AppBar + 五 Tab、记忆召回统计三卡 + 命中条目、笔记/复盘/任务渲染、错误态恢复。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    _mockAgentMind(api);
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
        home: const AgentMindScreen(characterId: 1, characterName: 'Alpha'),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(AgentMindScreen)))!;

  Future<void> tapTab(WidgetTester tester, AppLocalizations l10n, int index) async {
    await tester.tap(find.text([
      l10n.agentMindMemorySearch,
      l10n.agentMindRunningNotes,
      l10n.agentMindReflection,
      l10n.agentMindTasks,
      l10n.agentMindToolLogs,
    ][index]).last);
    await tester.pumpAndSettle();
  }

  testWidgets('AgentMindScreen builds with 5 tabs', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.agentMind), findsOneWidget);
    expect(find.text(l10n.agentMindMemorySearch), findsWidgets);
    expect(find.text(l10n.agentMindRunningNotes), findsWidgets);
    expect(find.text(l10n.agentMindReflection), findsWidgets);
    expect(find.text(l10n.agentMindTasks), findsWidgets);
    expect(find.text(l10n.agentMindToolLogs), findsWidgets);
  });

  testWidgets('memory search tab: 3 stat cards + hit entries in AuroraCard',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    // 统计三卡：命中 7 / 未命中 3 / 平均 42ms
    expect(find.text('7'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('42ms'), findsOneWidget);
    expect(find.text(l10n.agentMindStatHit), findsOneWidget);
    expect(find.text(l10n.agentMindStatMiss), findsOneWidget);
    expect(find.text(l10n.agentMindStatAvgLatency), findsOneWidget);
    // 命中条目 AuroraCard
    expect(find.text('recent query'), findsOneWidget);
    expect(find.byType(AuroraCard), findsAtLeastNWidgets(4));
  });

  testWidgets('notes/reflection/tasks/toollogs tabs render via switching',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    // 运行笔记
    await tapTab(tester, l10n, 1);
    expect(find.text(l10n.agentMindIdentity), findsOneWidget);
    expect(find.text('I like quiet mornings'), findsOneWidget);

    // 复盘（时间线单条）
    await tapTab(tester, l10n, 2);
    expect(find.textContaining('weekly reflection content'), findsOneWidget);
    expect(find.byType(AuroraCard), findsOneWidget);

    // 任务（时间线）
    await tapTab(tester, l10n, 3);
    expect(find.text('organize photos'), findsOneWidget);

    // 工具日志（时间线 + 汇总）
    await tapTab(tester, l10n, 4);
    expect(find.textContaining('search / plugin'), findsOneWidget);
  });

  testWidgets('error state -> loadFailed + retry recovers', (tester) async {
    api.json('GET', '/api/v1/characters/1/agent-mind', {'detail': 'boom'},
        status: 500);
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.byType(EmptyState), findsOneWidget);
    expect(find.text(l10n.loadFailed), findsOneWidget);
    expect(find.text(l10n.retry), findsOneWidget);

    _mockAgentMind(api);
    await tester.tap(find.text(l10n.retry));
    await tester.pumpAndSettle();
    expect(find.byType(EmptyState), findsNothing);
    expect(find.text('7'), findsOneWidget);
  });
}

Map<String, Object?> _agentMindData() => {
      'memory_search': {
        'total': 10,
        'hit': 7,
        'miss': 3,
        'avg_latency_ms': 42,
        'recent': [
          {
            'query': 'recent query',
            'hit_count': 2,
            'returned': 3,
            'created_at': '2026-08-28T02:00:00Z',
            'latency_ms': 30,
          },
        ],
      },
      'running_notes': {
        'identity': {
          'content': 'I like quiet mornings',
          'updated_at': '2026-08-28T02:00:00Z',
        },
        'pinned': [
          {
            'label': 'note',
            'content': 'keep the desk tidy',
            'updated_at': '2026-08-28T02:00:00Z',
          },
        ],
      },
      'reflection': {
        'content': 'weekly reflection content',
        'created_at': '2026-08-28T02:00:00Z',
      },
      'tasks': [
        {
          'goal': 'organize photos',
          'trigger': 'manual',
          'status': 'running',
          'created_at': '2026-08-28T02:00:00Z',
        },
      ],
      'tool_logs': [
        {
          'trigger': 'chat',
          'route': 'search / plugin',
          'status': 'ok',
          'steps': 'step1',
          'created_at': '2026-08-28T02:00:00Z',
          'latency_ms': 120,
        },
        {
          'trigger': 'chat',
          'route': 'search / fail',
          'status': 'failed',
          'steps': '',
          'created_at': '2026-08-28T02:01:00Z',
          'latency_ms': 60,
        },
      ],
    };

void _mockAgentMind(FakeApiAdapter api) {
  api.json('GET', '/api/v1/characters/1/agent-mind', _agentMindData());
}
