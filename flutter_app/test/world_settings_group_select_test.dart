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

/// 世界设定页分组＋批量勾选删除（A8 / 2026-10-11 用户点名「加上批量勾选删除」）。
///
/// 钉住的行为：
/// - 分组只认 `author`：user 的进「我的设定」，system（角色自己编纂的）进「TA 记下的·待你确认」，
///   并带上「改一下保存＝确认」的提示（这条与批 67 的 A55 修复配套：确认后条目当场换组、且不改层归属）；
/// - 多选模式下行首是勾选框、编辑/删除按钮收起（防误触），顶栏显示已选数量并可全选/取消；
/// - 批量删除＝对每条各发一个 DELETE（逐条实测 0.01–0.05 秒，不为它加后端批量端点），
///   单条失败不中断整批，末尾如实提示「有 N 条没删掉」；
/// - 多选模式下返回键先退出多选，不会直接把整页关掉；
/// - 副标题不露后端字段名（原来是 "author: system · curated"）。
void main() {
  late FakeApiAdapter api;

  const int charId = 1;
  const String listPath = '/api/v1/characters/1/world-facts';

  Map<String, dynamic> fact({
    required int id,
    required String value,
    required String author,
    String predicate = 'curated',
  }) =>
      {
        'id': id,
        'subject_type': 'character',
        'subject_id': charId,
        'predicate': predicate,
        'object_value': value,
        'author': author,
        'is_authoritative': author == 'user',
        'epistemic_status': 'FACT',
        'asserted_at': '2026-10-11T10:00:00',
      };

  final mine = fact(id: 11, value: '我住在杭州', author: 'user', predicate: 'setting');
  final pendingA = fact(id: 12, value: '用户腰有旧伤', author: 'system');
  final pendingB = fact(id: 13, value: '用户喜欢安静', author: 'system');

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    api.json('GET', listPath, {
      'items': [mine, pendingA, pendingB],
    });
  });

  Widget app() => ChangeNotifierProvider<SettingsProvider>(
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

  Future<void> enterSelect(WidgetTester tester) async {
    await tester.tap(find.byIcon(Icons.checklist));
    await tester.pumpAndSettle();
  }

  /// 顶栏那个批量删除按钮（`find.byIcon` 命中的是里面的 Icon，要往上取 IconButton 才拿得到 onPressed）。
  IconButton batchDeleteButton(WidgetTester tester) => tester.widget<IconButton>(
        find.ancestor(
          of: find.byIcon(Icons.delete_outline),
          matching: find.byType(IconButton),
        ),
      );

  testWidgets('按作者分成两组，待确认组带「改一下保存＝确认」提示', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.worldFactsGroupMine), findsOneWidget);
    expect(find.text(l10n.worldFactsGroupPending), findsOneWidget);
    expect(find.text(l10n.worldFactsPendingHint), findsOneWidget);
    expect(find.text('我住在杭州'), findsOneWidget);      // 我写的
    expect(find.text('用户腰有旧伤'), findsOneWidget);     // TA 记下的
    // 界面上不露后端字段名
    expect(find.textContaining('author:'), findsNothing);
    expect(find.textContaining('curated'), findsNothing);
  });

  testWidgets('多选：行首变勾选框、编辑与删除按钮收起，未选中时删除不可点', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.byType(Checkbox), findsNothing);
    await enterSelect(tester);

    expect(find.byType(Checkbox), findsNWidgets(3));
    expect(find.byIcon(Icons.edit_outlined), findsNothing);   // 防误触
    expect(find.byIcon(Icons.delete_outline), findsNWidgets(1)); // 只剩顶栏那一个批量删除
    expect(tester.widget<Checkbox>(find.byType(Checkbox).first).value, isFalse);
    expect(batchDeleteButton(tester).onPressed, isNull,
        reason: '一条都没选就不该能点删除');
    expect(find.text(l10n.worldFactSelectedCount(0)), findsOneWidget);

    await tester.tap(find.byType(Checkbox).first);
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactSelectedCount(1)), findsOneWidget);
    expect(batchDeleteButton(tester).onPressed, isNotNull);
  });

  testWidgets('全选与取消全选', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await enterSelect(tester);
    await tester.tap(find.text(l10n.worldFactSelectAll));
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactSelectedCount(3)), findsOneWidget);
    for (final box in tester.widgetList<Checkbox>(find.byType(Checkbox))) {
      expect(box.value, isTrue);
    }

    await tester.tap(find.text(l10n.worldFactDeselectAll));
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactSelectedCount(0)), findsOneWidget);
  });

  testWidgets('批量删除：选中两条 → 确认 → 各发一条 DELETE 并重拉列表', (tester) async {
    api.json('DELETE', '$listPath/12', {'ok': true});
    api.json('DELETE', '$listPath/13', {'ok': true});
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await enterSelect(tester);
    await tester.tap(find.text('用户腰有旧伤'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('用户喜欢安静'));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.delete_outline));
    await tester.pumpAndSettle();
    expect(find.text(l10n.worldFactDeleteSelectedTitle(2)), findsOneWidget);

    api.requests.clear();
    await tester.tap(find.text(l10n.delete));
    await tester.pumpAndSettle();

    final deleted = api.requests
        .where((r) => r.method == 'DELETE')
        .map((r) => r.path)
        .toList();
    expect(deleted, containsAll(<String>['$listPath/12', '$listPath/13']));
    expect(deleted.length, 2, reason: '只删选中的两条，不碰「我的设定」');
    expect(api.requests.where((r) => r.method == 'GET' && r.path == listPath), isNotEmpty,
        reason: '删完要重新拉一次，列表不能停在旧快照');
    // 删除成功后退出多选模式，不留下半截勾选状态
    expect(find.byType(Checkbox), findsNothing);
  });

  testWidgets('批量删除：单条失败不中断整批，并如实报「有 N 条没删掉」', (tester) async {
    api.json('DELETE', '$listPath/11', {'ok': true});
    api.json('DELETE', '$listPath/13', {'ok': true});
    // 12 号不注册 → FakeApiAdapter 回 404（这一条删除会抛异常）
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await enterSelect(tester);
    await tester.tap(find.text(l10n.worldFactSelectAll));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.delete_outline));
    await tester.pumpAndSettle();
    await tester.tap(find.text(l10n.delete));
    await tester.pumpAndSettle();

    final deleted = api.requests.where((r) => r.method == 'DELETE').map((r) => r.path).toList();
    expect(deleted, contains('$listPath/13'), reason: '排在失败那条后面的条目必须照删');
    expect(deleted, contains('$listPath/11'));
    expect(deleted, contains('$listPath/12'));
    expect(deleted.length, 3);
    expect(find.text(l10n.worldFactDeleteFailed(1)), findsOneWidget);
  });

  testWidgets('多选模式下系统返回键先退出多选，不直接离开这一页', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    await enterSelect(tester);
    expect(find.byType(Checkbox), findsNWidgets(3));

    await tester.binding.handlePopRoute();   // 系统返回：不点返回按钮（本页是 home，没有返回按钮）
    await tester.pumpAndSettle();
    // 勾选框收起＝回到普通浏览态，页面还在
    expect(find.byType(Checkbox), findsNothing);
    expect(find.byType(WorldSettingsScreen), findsOneWidget);
    expect(find.byIcon(Icons.checklist), findsOneWidget);
  });
}
