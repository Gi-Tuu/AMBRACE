// 设置页「模型与 API」（2026-11-22）Widget 守卫：列表渲染（默认/共享/停用徽章）＋
// 用量瓦片＋设默认动作端点＋新增表单 body 契约（api_key 留空不上送）。全部合成数据。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/settings_pane.dart';
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

final List<(String, String)> calls = []; // (method, path)
final List<Map<String, dynamic>> bodies = [];

Map<String, dynamic> cfgJson(int id, {bool shared = false, bool off = false}) =>
    {
      'id': id,
      'user_id': shared ? 3 : 4,
      'name': shared ? 'CFG_SHARED' : 'CFG_$id',
      'base_url': 'https://api.example.com/v1',
      'api_key': shared ? '' : 'sk-1***abcd',
      'model': 'synth-model',
      'provider': 'synth',
      'enabled': !off,
      'is_default': id == 1 && !shared,
      'shared_with_subs': shared,
      'has_api_key': true,
      'is_shared': shared,
      'created_at': '2026-11-22T00:00:00',
      'updated_at': '2026-11-22T00:00:00',
    };

const usageJson = {
  'total_limit': 1000000,
  'limit_source': 'global',
  'used_total': 400000,
  'remaining': 600000,
  'today': 118189,
  'week': 754626,
  'month': 2984852,
  'by_model': [
    {'model': 'synth-model', 'total': 400000},
  ],
};

Future<ResponseBody> handler(RequestOptions o) async {
  calls.add((o.method, o.path));
  if (o.method == 'POST' || o.method == 'PUT') {
    bodies.add(Map<String, dynamic>.from(o.data as Map));
  }
  if (o.path == '/api/v1/auth/profile') return reply({'id': 4, 'nickname': 'TESTER'});
  if (o.path == '/api/v1/system/llm-usage') return reply(usageJson);
  if (o.path == '/api/v1/llm-configs' && o.method == 'GET') {
    return reply({'items': [cfgJson(1), cfgJson(2, shared: true), cfgJson(3, off: true)]});
  }
  if (o.path == '/api/v1/llm-configs' && o.method == 'POST') {
    return reply(cfgJson(9));
  }
  if (o.path == '/api/v1/llm-configs/1/default') return reply(cfgJson(1));
  if (o.path.startsWith('/api/v1/llm-configs/') && o.path.endsWith('/test')) {
    return reply(
        {'ok': true, 'latency_ms': 321, 'model': 'synth-model', 'api_key_tail': 'abcd'});
  }
  if (o.path.startsWith('/api/v1/llm-configs/') && o.method == 'PUT') {
    return reply(cfgJson(9));
  }
  if (o.path == '/api/v1/system/api-config/tasks') {
    return reply({'tasks': [
      {'task': 'memory', 'name': 'SYNTH_TASK_MEMORY', 'desc': 'SYNTH_TASK_DESC'},
      {'task': 'diary', 'name': 'SYNTH_TASK_DIARY', 'desc': 'SYNTH_TASK_DESC2'},
    ]});
  }
  if (o.path == '/api/v1/system/api-config/task/memory' && o.method == 'GET') {
    return reply({'enabled': false, 'base_url': null, 'model': null,
        'provider': null, 'has_api_key': false, 'configured': false});
  }
  if (o.path == '/api/v1/system/api-config/task/diary' && o.method == 'GET') {
    return reply({'enabled': true, 'base_url': 'https://api.example.com/v1',
        'model': 'SYNTH_TASK_MODEL', 'provider': 'synth', 'has_api_key': true,
        'configured': true});
  }
  if (o.path.startsWith('/api/v1/system/api-config/task/') && o.method == 'PUT') {
    return reply({'status': 'ok', 'task': 'memory', 'enabled': true,
        'configured': true});
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpPane(WidgetTester tester) async {
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  tester.view.physicalSize = const Size(1400, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SettingsPane(
        client: client,
        myUserId: 4,
        lang: 'zh',
        themeMode: ThemeMode.light,
        onTheme: (_) {},
        onLang: (_) {},
        onLogout: () {},
        rs: const ReaderStrings('zh'),
        cs: const CoreStrings('zh'),
        skinId: 'ios',
        onSkin: (_) {},
        seedIndex: -1,
        onSeed: (_) {},
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    calls.clear();
    bodies.clear();
  });

  testWidgets('配置列表渲染：名称/徽章/用量瓦片', (tester) async {
    await pumpPane(tester);
    expect(find.text('CFG_1'), findsOneWidget);
    expect(find.text('CFG_SHARED'), findsOneWidget);
    expect(find.text('CFG_3'), findsOneWidget);
    // 徽章：默认×1、共享×1、停用×1
    expect(find.text('默认'), findsNWidgets(2)); // LLM 默认徽章＋diary 任务绑定徽标
    expect(find.text('共享'), findsOneWidget);
    expect(find.text('停用'), findsOneWidget);
    // 用量瓦片（千分位）
    expect(find.text('118,189'), findsOneWidget);
    expect(find.text('400,000'), findsOneWidget);
    expect(find.textContaining('600,000'), findsOneWidget); // 与「剩余额度」同串
  });

  testWidgets('共享配置动作面板只读：无变更动作', (tester) async {
    await pumpPane(tester);
    await tester.tap(find.text('CFG_SHARED')); // 共享配置 → 动作面板应只有只读提示
    await tester.pumpAndSettle();
    expect(find.text('共享配置只读'), findsNWidgets(2)); // 面板头副标题＋只读说明行
    expect(find.text('设为默认'), findsNothing); // 共享配置不提供变更动作
    expect(find.text('删除'), findsNothing);
    await tester.tapAt(const Offset(10, 10)); // 点背景收起面板
    await tester.pumpAndSettle();
  });

  testWidgets('自有配置打开动作面板并可设默认', (tester) async {
    await pumpPane(tester);
    await tester.tap(find.text('CFG_3'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('设为默认'));
    await tester.pumpAndSettle();
    expect(calls, contains(('POST', '/api/v1/llm-configs/3/default')));
  });

  testWidgets('新增表单：填名称保存 → POST body 无空 api_key', (tester) async {
    await pumpPane(tester);
    await tester.tap(find.text('新增配置'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, '名称'), 'MY_NEW_CFG');
    await tester.enterText(
        find.widgetWithText(TextField, '模型'), 'synth-model-x');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    final post = calls.singleWhere((c) => c.$1 == 'POST' && c.$2 == '/api/v1/llm-configs');
    expect(post, isNotNull);
    final body = bodies.last;
    expect(body['name'], 'MY_NEW_CFG');
    expect(body.containsKey('api_key'), false); // 留空不上送
    expect(body['enabled'], true);
  });

  testWidgets('任务模型配置：目录渲染 + 绑定态徽标', (tester) async {
    await pumpPane(tester);
    expect(find.text('SYNTH_TASK_MEMORY'), findsOneWidget);
    expect(find.text('SYNTH_TASK_DIARY'), findsOneWidget);
    expect(find.text('未配置'), findsOneWidget); // memory 未配置
    // diary 已绑定（configured=true）：行尾显示「默认」徽标
    expect(find.text('默认'), findsNWidgets(2)); // LLM 默认配置 + diary 任务绑定
  });

  testWidgets('任务模型配置：编辑保存 → PUT body 无空 api_key', (tester) async {
    await pumpPane(tester);
    await tester.tap(find.text('SYNTH_TASK_MEMORY'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, '模型'), 'TASK_MODEL_X');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    final put = calls.singleWhere(
        (c) => c.$1 == 'PUT' && c.$2 == '/api/v1/system/api-config/task/memory');
    expect(put, isNotNull);
    final body = bodies.last;
    expect(body['model'], 'TASK_MODEL_X');
    expect(body.containsKey('api_key'), false); // 留空不上送＝保留现值
  });
}
