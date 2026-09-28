// S2 上下文注入长度档位页（2026-09-28）：三档渲染 / 当前档标记 / tier_source 三态文案 /
// 无样本占位 / 切档 PUT + 重新 GET 刷新 / GET 失败错误态与重试。
// 数值一律由 fixture 提供，用例断言「页面回显服务端给的数」而非前端算出的数。
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/settings/context_budget_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/api_client.dart';

import '../../fake_api_adapter.dart';

const _get = '/api/v1/system/context-budget';
const _put = '/api/v1/system/context-budget/tier';

/// 构造一份与后端 GET /api/v1/system/context-budget 字段一一对应的读数。
Map<String, dynamic> _payload({
  String tier = 'standard',
  String tierSource = 'default',
  Map<String, dynamic>? lastUsage,
  Map<String, dynamic>? lastClip,
  int clipCount24h = 0,
  int effective = 6400,
}) {
  const budgets = {'standard': 9000, 'extended': 13000, 'max': 18000};
  return {
    'status': 'ok',
    'total_quota_tokens': 9000,
    'reserve_reply_tokens': 1200,
    'reserve_tools_tokens': 900,
    'floor_tokens': 256,
    'tier': tier,
    'tier_source': tierSource,
    'tier_stored': tierSource == 'user' ? tier : null,
    'tier_error': '',
    'tier_budget_tokens': budgets[tier],
    'tier_ceiling_tokens': 20000,
    'tier_options': [
      for (final key in ['standard', 'extended', 'max'])
        {'key': key, 'budget_tokens': budgets[key], 'is_current': key == tier},
    ],
    'effective_budget_tokens': effective,
    'flag_enabled': true,
    'last_usage': lastUsage ??
        {
          'status': 'unknown',
          'reason': 'no_sample',
          'system_chars': null,
          'est_tokens': null
        },
    'last_clip': lastClip,
    'clip_count_24h': clipCount24h,
    'error': '',
  };
}

void main() {
  late FakeApiAdapter api;
  late Map<String, dynamic> current;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    current = _payload();
    api.handle('GET', _get, (_) => FakeApiAdapter.body(current));
    api.handle('PUT', _put, (options) {
      final raw = options.data;
      final body = Map<String, dynamic>.from(
          raw is String ? jsonDecode(raw) as Map : raw as Map);
      current = _payload(
          tier: body['tier'] as String, tierSource: 'user', effective: 10400);
      return FakeApiAdapter.body({
        'status': 'ok',
        'tier': body['tier'],
        'tier_budget_tokens': current['tier_budget_tokens'],
        'previous_tier': 'standard',
      });
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
          home: const ContextBudgetScreen(),
        ),
      );

  /// 打开页面并等首帧 GET 完成。
  Future<void> open(WidgetTester tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
  }

  /// 当前选中档的档位名（RadioGroup 的 groupValue 对上那条 RadioListTile）。
  String selectedTier(WidgetTester tester) {
    final group = tester
        .widget<RadioGroup<String>>(find.byType(RadioGroup<String>).first);
    final tiles = tester
        .widgetList<RadioListTile<String>>(find.byType(RadioListTile<String>));
    final tile = tiles.firstWhere((t) => t.value == group.groupValue);
    return (tile.title! as Text).data ?? '';
  }

  testWidgets('三档全部渲染，token 数取服务端 tier_options', (tester) async {
    await open(tester);
    expect(find.text('标准'), findsOneWidget);
    expect(find.text('加长'), findsOneWidget);
    expect(find.text('最大'), findsOneWidget);
    // 三档数值来自响应：9000 / 13000 / 18000
    expect(find.text('9000 tokens'), findsOneWidget);
    expect(find.text('13000 tokens'), findsOneWidget);
    expect(find.text('18000 tokens'), findsOneWidget);
  });

  testWidgets('当前档用 is_current 标记，来源 default 说「默认」', (tester) async {
    await open(tester);
    expect(find.text('当前'), findsOneWidget);
    expect(selectedTier(tester), '标准');
    expect(find.text('默认（未单独设置）'), findsOneWidget);
    expect(find.text('已按你的选择'), findsNothing);
  });

  testWidgets('tier_source=user 显示「已按你的选择」', (tester) async {
    current = _payload(tier: 'extended', tierSource: 'user');
    await open(tester);
    expect(find.text('已按你的选择'), findsOneWidget);
    expect(selectedTier(tester), '加长');
    expect(find.text('当前'), findsOneWidget);
  });

  testWidgets('tier_source=unavailable 显示读不到、回落默认值', (tester) async {
    current = _payload(tierSource: 'unavailable');
    await open(tester);
    expect(find.text('暂时读不到，显示默认值'), findsOneWidget);
  });

  testWidgets('无样本时只写「暂无样本」，不拿预算数冒充占用', (tester) async {
    current = _payload(effective: 6400);
    await open(tester);
    expect(find.text('暂无样本'), findsOneWidget);
    // 占用行没有数字：6400 只出现在「本档实际生效预算」一处
    expect(find.text('6400 tokens'), findsOneWidget);
    expect(find.text('最近没有发生裁剪'), findsOneWidget);
  });

  testWidgets('有样本与裁剪记录时回显服务端读数', (tester) async {
    current = _payload(
      tierSource: 'user',
      lastUsage: {
        'status': 'ok',
        'est_tokens': 4321,
        'created_at': '2026-09-28 01:02:03'
      },
      lastClip: {'character_id': 7, 'created_at': '2026-09-27 22:10:00'},
      clipCount24h: 3,
    );
    await open(tester);
    expect(find.text('4321 tokens'), findsOneWidget);
    expect(find.text('2026-09-27 22:10:00'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
  });

  testWidgets('读数区展示预留口径与上限（全部来自服务端）', (tester) async {
    await open(tester);
    expect(find.text('本档实际生效预算'), findsOneWidget);
    expect(find.text('档位上限'), findsOneWidget);
    expect(find.text('20000 tokens'), findsOneWidget);
    expect(find.text('预留：回复'), findsOneWidget);
    expect(find.text('1200 tokens'), findsOneWidget);
    expect(find.text('预留：工具定义'), findsOneWidget);
    expect(find.text('900 tokens'), findsOneWidget);
    expect(find.text('预算下限'), findsOneWidget);
    expect(find.text('256 tokens'), findsOneWidget);
    expect(find.text('代价提示'), findsOneWidget);
  });

  testWidgets('切档：发 PUT 后重新 GET，页面与服务端返回一致', (tester) async {
    await open(tester);
    api.requests.clear();
    await tester.tap(find.text('加长'));
    await tester.pumpAndSettle();

    expect(api.requests, contains((method: 'PUT', path: _put)));
    expect(api.requests.where((r) => r.method == 'GET').length, 1,
        reason: '切档后重拉一次刷新');
    expect(find.text('已按你的选择'), findsOneWidget);
    expect(selectedTier(tester), '加长');
    expect(find.text('10400 tokens'), findsOneWidget);
    expect(find.text('档位已保存'), findsOneWidget);
  });

  testWidgets('切档失败：SnackBar 提示，不改成本地假状态', (tester) async {
    await open(tester);
    api.handle(
        'PUT', _put, (_) => FakeApiAdapter.body({'detail': 'nope'}, 500));
    await tester.tap(find.text('最大'));
    await tester.pumpAndSettle();
    expect(find.text('档位保存失败，请重试'), findsOneWidget);
    expect(selectedTier(tester), '标准');
  });

  testWidgets('GET 失败显示错误态 + 重试，不白屏', (tester) async {
    api.handle(
        'GET', _get, (_) => FakeApiAdapter.body({'detail': 'boom'}, 500));
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('contextBudgetError')), findsOneWidget);
    expect(find.text('读取上下文预算失败'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);

    // 恢复后点重试能正常渲染
    api.handle('GET', _get, (_) => FakeApiAdapter.body(current));
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('注入档位'), findsOneWidget);
    expect(find.text('标准'), findsOneWidget);
  });
}
