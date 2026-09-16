import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/character/world_settings_screen.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/api_client.dart';

import 'fake_api_adapter.dart';

/// 世界设定「修正历史」前端测试（小增量 2026-09-16，只读展开）：
/// - 默认收起：不发历史请求、不显示面板；
/// - 点「历史」→ GET .../world-facts/{id}/history → 列出当前 + 已取代版本（值/状态/作者/时间）；
/// - 失败：走 l10n 文案提示、收回展开态且不缓存空结果（可再次点击重试）；
/// - 只读：全程不产生任何写请求（无回滚/写历史入口）。
void main() {
  late FakeApiAdapter api;

  const int charId = 1;
  const int factId = 11;
  const String listPath = '/api/v1/characters/1/world-facts';
  const String historyPath = '/api/v1/characters/1/world-facts/11/history';

  const fact = <String, dynamic>{
    'id': factId,
    'subject_type': 'character',
    'subject_id': charId,
    'predicate': 'setting',
    'object_value': '我住在杭州',
    'author': 'user',
    'is_authoritative': true,
    'epistemic_status': 'FACT',
    'asserted_at': '2026-09-16T10:00:00',
  };

  const activeVersion = <String, dynamic>{
    'id': 12,
    'object_value': '我搬到了上海',
    'status': 'active',
    'author': 'user',
    'source': 'user_setting',
    'is_authoritative': true,
    'epistemic_status': 'FACT',
    'asserted_at': '2026-09-16T12:00:00',
    'superseded_at': null,
    'superseded_by': null,
  };

  const supersededVersion = <String, dynamic>{
    'id': factId,
    'object_value': '我住在杭州',
    'status': 'superseded',
    'author': 'system',
    'source': 'curated',
    'is_authoritative': false,
    'epistemic_status': 'FACT',
    'asserted_at': '2026-09-10T09:00:00',
    'superseded_at': '2026-09-16T12:00:00',
    'superseded_by': 12,
  };

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    api.json('GET', listPath, {
      'items': [fact],
    });
  });

  Widget app() => ChangeNotifierProvider<SettingsProvider>(
        // IosCardGroup 需要 SettingsProvider（皮肤/动效），与 main.dart 一致包在 MaterialApp 外
        create: (_) => SettingsProvider(),
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const WorldSettingsScreen(characterId: charId),
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(WorldSettingsScreen)))!;

  testWidgets('默认收起；点历史后列出当前 + 已取代版本，且全程无写请求', (tester) async {
    api.json('GET', historyPath, {
      'current': activeVersion,
      'versions': [activeVersion, supersededVersion],
      'truncated': false,
    });

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text('我住在杭州'), findsOneWidget);
    expect(find.byIcon(Icons.history), findsOneWidget);
    // 默认收起：不请求历史、不显示面板
    expect(find.text(l10n.worldFactHistory), findsNothing);
    expect(api.requests.where((r) => r.path == historyPath), isEmpty);

    await tester.tap(find.byIcon(Icons.history));
    await tester.pumpAndSettle();

    expect(find.text(l10n.worldFactHistory), findsOneWidget);              // 面板标题
    expect(find.text(l10n.worldFactHistoryCurrentLabel), findsOneWidget);  // 当前
    expect(find.text(l10n.worldFactHistorySuperseded), findsOneWidget);    // 已取代
    expect(find.text('我搬到了上海'), findsOneWidget);                       // 当前值
    expect(find.text('我住在杭州'), findsNWidgets(2));                       // 列表行 + 旧版
    expect(find.textContaining('system'), findsWidgets);                   // 作者保留来源
    expect(api.requests.where((r) => r.path == historyPath).length, 1);
    // 只读：无回滚/写历史入口（全程无 PUT/POST/DELETE）
    expect(api.requests.where((r) => r.method != 'GET'), isEmpty);
  });

  testWidgets('再次点击收起（不重复请求）；重新展开用缓存不再请求', (tester) async {
    api.json('GET', historyPath, {
      'current': activeVersion,
      'versions': [activeVersion, supersededVersion],
      'truncated': false,
    });

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await tester.tap(find.byIcon(Icons.history));
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactHistory), findsOneWidget);

    await tester.tap(find.byIcon(Icons.history));   // 收起
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactHistory), findsNothing);

    await tester.tap(find.byIcon(Icons.history));   // 再展开：命中缓存
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactHistory), findsOneWidget);
    expect(api.requests.where((r) => r.path == historyPath).length, 1);
  });

  testWidgets('历史拉取失败：l10n 文案提示、不假装「没改过」、可重试', (tester) async {
    // 未注册历史路由 → FakeApiAdapter 默认 404
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await tester.tap(find.byIcon(Icons.history));
    await tester.pumpAndSettle();

    expect(find.text(l10n.worldFactHistoryLoadFailed), findsOneWidget);  // SnackBar 文案
    expect(find.text(l10n.worldFactHistoryEmpty), findsNothing);         // 不伪装成「还没被修改过」
    expect(find.text(l10n.worldFactHistory), findsNothing);              // 失败即收回展开态

    await tester.tap(find.byIcon(Icons.history));
    await tester.pumpAndSettle();

    expect(api.requests.where((r) => r.path == historyPath).length, 2);  // 未缓存空结果 → 重试
  });
}
