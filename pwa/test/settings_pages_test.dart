// 设置页三期（2026-11-22 批 4）页面守卫：更新公告按天折叠（最新默认展开）、
// 功能开关分组渲染＋切换 PUT body、上下文预算档位 ChoiceChip＋PUT {tier}。
// 全部合成数据（SYNTH_ 前缀）。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/context_budget_page.dart';
import 'package:ambrace_pwa/features/reader/feature_flags_page.dart';
import 'package:ambrace_pwa/features/reader/updates_page.dart';
import 'package:ambrace_pwa/l10n/reader_strings.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeAdapter implements HttpClientAdapter {
  FakeAdapter(this.handle);
  final Future<ResponseBody> Function(RequestOptions) handle;

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? body,
          Future<void>? cancel) =>
      handle(o);

  @override
  void close({bool force = false}) {}
}

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

final List<(String, String)> calls = [];
final List<Map<String, dynamic>> bodies = [];

Future<ResponseBody> handler(RequestOptions o) async {
  calls.add((o.method, o.path));
  if (o.method == 'PUT') bodies.add(Map<String, dynamic>.from(o.data as Map));
  if (o.path == '/api/v1/system/updates') {
    return reply({'days': [
      {'date': '2026-11-22', 'title': 'SYNTH_DAY1', 'items': [
        {'content': 'SYNTH_ITEM_A', 'reason': 'SYNTH_WHY_A'},
      ]},
      {'date': '2026-11-21', 'title': 'SYNTH_DAY2', 'items': [
        {'content': 'SYNTH_ITEM_B', 'reason': 'SYNTH_WHY_B'},
      ]},
    ]});
  }
  if (o.path == '/api/v1/system/feature-flags') {
    return reply({'status': 'ok', 'flags': [
      {'key': 'synth_flag_user', 'enabled': false, 'value': false, 'type': 'bool',
       'source': 'default', 'scope': 'user', 'user_enabled': null,
       'meta': {'title': 'SYNTH_FLAG_U', 'desc': 'SYNTH_FLAG_U_DESC',
                'group': 'synth_group_a', 'group_order': 1, 'order': 1,
                'visible': true}},
      {'key': 'synth_flag_srv', 'enabled': true, 'value': true, 'type': 'bool',
       'source': 'default', 'scope': 'server', 'user_enabled': null,
       'meta': {'title': 'SYNTH_FLAG_S', 'desc': 'SYNTH_FLAG_S_DESC',
                'group': 'synth_group_b', 'group_order': 2, 'order': 2,
                'visible': true}},
    ]});
  }
  if (o.path == '/api/v1/system/feature-flags/synth_flag_user' && o.method == 'PUT') {
    return reply({'key': 'synth_flag_user', 'enabled': true, 'scope': 'user',
        'user_enabled': true});
  }
  if (o.path == '/api/v1/system/context-budget') {
    return reply({
      'tier': 'standard',
      'tier_options': [
        {'key': 'standard', 'budget_tokens': 9000, 'is_current': true},
        {'key': 'max', 'budget_tokens': 18000, 'is_current': false},
      ],
      'total_quota_tokens': 9000,
      'effective_budget_tokens': 9000,
      'clip_count_24h': 3,
      'last_usage': {'status': 'ok', 'est_tokens': 4472},
      'section_breakdown': {'status': 'ok', 'samples': 6, 'items': [
        {'key': 'chat_history', 'samples': 6, 'avg_chars': 1238,
         'max_chars': 1699, 'empty_count': 0, 'share': 1.0},
      ]},
    });
  }
  if (o.path == '/api/v1/system/context-budget/tier' && o.method == 'PUT') {
    return reply({'tier': 'max'});
  }
  return reply({'detail': 'not_found'}, 404);
}

/// 统一引导：同一个假 adapter 客户端交给页面构造器（页面自带 client 参数）。
Future<void> pumpPage(
    WidgetTester tester, Widget Function(AmbApiClient client) builder) async {
  tester.view.physicalSize = const Size(1400, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(home: builder(client)));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    calls.clear();
    bodies.clear();
  });

  testWidgets('更新公告：按天折叠，最新一天默认展开', (tester) async {
    await pumpPage(tester, (c) => UpdatesPage(client: c, rs: const ReaderStrings('zh')));
    expect(find.text('SYNTH_DAY1 · 2026-11-22'), findsOneWidget);
    expect(find.textContaining('SYNTH_ITEM_A'), findsOneWidget);
    expect(find.textContaining('SYNTH_WHY_A'), findsOneWidget);
    // 次日默认折叠：标题在、正文不在
    expect(find.text('SYNTH_DAY2 · 2026-11-21'), findsOneWidget);
    expect(find.textContaining('SYNTH_ITEM_B'), findsNothing);
  });

  testWidgets('功能开关：分组渲染 + 切换 PUT {enabled:true}', (tester) async {
    await pumpPage(
        tester, (c) => FeatureFlagsPage(client: c, rs: const ReaderStrings('zh')));
    expect(find.text('SYNTH_FLAG_U'), findsOneWidget);
    expect(find.text('SYNTH_FLAG_S'), findsOneWidget);
    expect(find.textContaining('synth_group_a'), findsOneWidget);
    await tester.tap(find.text('SYNTH_FLAG_U'));
    await tester.pumpAndSettle();
    expect(calls, contains(('PUT', '/api/v1/system/feature-flags/synth_flag_user')));
    expect(bodies.last['enabled'], true);
  });

  testWidgets('上下文预算：档位 ChoiceChip 选择 → PUT {tier}', (tester) async {
    await pumpPage(tester,
        (c) => ContextBudgetPage(client: c, rs: const ReaderStrings('zh')));
    expect(find.textContaining('standard · 9,000'), findsOneWidget);
    expect(find.textContaining('4,472'), findsOneWidget); // 最近一轮
    expect(find.textContaining('chat_history'), findsOneWidget); // 分层体量
    await tester.tap(find.text('max · 18,000'));
    await tester.pumpAndSettle();
    expect(calls, contains(('PUT', '/api/v1/system/context-budget/tier')));
    expect(bodies.last['tier'], 'max');
  });
}
