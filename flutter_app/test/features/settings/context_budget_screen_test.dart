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
  Map<String, dynamic>? sectionBreakdown,
  Map<String, dynamic>? costEstimate,
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
    // Y2 两段：默认「无样本 / 无价目」——服务端给不了数时，页面不许自己补
    'section_breakdown': sectionBreakdown ??
        {'status': 'no_sample', 'samples': 0, 'items': []},
    'cost_estimate': costEstimate ??
        {
          'status': 'unavailable',
          'reason': 'no_price_table',
          'currency': 'CNY',
          'budget_tokens': effective,
          'per_turn_low': null,
          'per_turn_high': null
        },
    'error': '',
  };
}

/// 后端 section_breakdown 的有样本形态（share 由服务端按峰值算好）。
Map<String, dynamic> _breakdownWithItems() => {
      'status': 'ok',
      'samples': 7,
      'samples_limit': 20,
      'sections_scope': 'top16_per_turn',
      'keys_total': 2,
      'items': [
        {
          'key': 'panorama_memory',
          'samples': 7,
          'avg_chars': 1500,
          'max_chars': 2000,
          'empty_count': 0,
          'share': 1.0
        },
        {
          'key': 'phone',
          'samples': 5,
          'avg_chars': 300,
          'max_chars': 640,
          'empty_count': 2,
          'share': 0.2
        },
      ],
    };

Map<String, dynamic> _costOk({int effective = 6400}) => {
      'status': 'ok',
      'reason': '',
      'currency': 'CNY',
      'basis': 'full_effective_budget_input_only',
      'budget_tokens': effective,
      'model': 'deepseek-v4-flash',
      'price_source': 'deepseek-v4-flash',
      'per_million_low': 1.0,
      'per_million_high': 2.0,
      'per_turn_low': effective / 1000000,
      'per_turn_high': effective * 2 / 1000000,
    };

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
    // 占用行与每层体量卡在无样本时同用一个空态词（两处 ⇒ 两条，都不许显示 0）
    expect(find.text('暂无样本'), findsNWidgets(2));
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

  // ── Y2：每层体量 + 费用估算（数值/比例全部来自服务端）──

  testWidgets('每层体量：有样本时按服务端 share 画条、回显均值/峰值/空次数', (tester) async {
    current = _payload(sectionBreakdown: _breakdownWithItems());
    await open(tester);
    expect(find.text('每层注入体量'), findsOneWidget);
    expect(find.text('最近 7 轮样本'), findsOneWidget);
    expect(find.text('panorama_memory'), findsOneWidget);
    expect(find.text('phone'), findsOneWidget);
    expect(find.text('均 1500 字 · 峰 2000 字'), findsOneWidget);
    expect(find.text('均 300 字 · 峰 640 字'), findsOneWidget);
    expect(find.text('2 轮为空'), findsOneWidget);
    // 空态词不再出现
    expect(find.text('暂无样本'), findsOneWidget, reason: '只剩占用行一处空态');
    final bars = tester
        .widgetList<LinearProgressIndicator>(find.byType(LinearProgressIndicator))
        .where((w) => w.value != null)
        .toList();
    expect(bars.map((w) => w.value), containsAllInOrder([1.0, 0.2]));
  });

  testWidgets('每层体量：无样本时不画任何条、不显示 0', (tester) async {
    current = _payload();
    await open(tester);
    expect(find.text('每层注入体量'), findsOneWidget);
    expect(
        tester
            .widgetList<LinearProgressIndicator>(find.byType(LinearProgressIndicator))
            .where((w) => w.value != null),
        isEmpty);
    expect(find.text('均 0 字 · 峰 0 字'), findsNothing);
  });

  testWidgets('费用估算：有价目时回显服务端区间与口径，前端不算数', (tester) async {
    current = _payload(costEstimate: _costOk(effective: 6400));
    await open(tester);
    expect(find.text('费用估算（每轮 · 区间）'), findsOneWidget);
    // 0.0064 / 0.0128 来自响应字段（6400 tokens × 每百万 1~2 元），页面只做小数位格式化
    expect(find.text('0.0064 ~ 0.0128 CNY'), findsOneWidget);
    expect(
        find.text('口径：按本档生效预算用满、只算输入侧，不含输出与工具调用'),
        findsOneWidget);
  });

  testWidgets('费用估算：缺价目如实标不可估算，不显示 0 元', (tester) async {
    current = _payload();
    await open(tester);
    expect(find.text('暂无法估算：服务端未配置单价'), findsOneWidget);
    expect(find.textContaining('~'), findsNothing);
  });

  testWidgets('费用估算：有价目表但该模型没收录 ⇒ 单独一条口径文案', (tester) async {
    current = _payload(costEstimate: {
      'status': 'unavailable',
      'reason': 'model_unpriced',
      'currency': 'CNY',
      'per_turn_low': null,
      'per_turn_high': null,
    });
    await open(tester);
    expect(find.text('暂无法估算：当前模型未收录单价'), findsOneWidget);
    expect(find.text('0.0000 ~ 0.0000 CNY'), findsNothing);
  });
}
