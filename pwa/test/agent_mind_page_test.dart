// PWA「AI 内心世界」页守卫（2026-11-24）：五个分区渲染 + 状态口径 + 折叠摘要。
// 全部合成数据（SYNTH_ 前缀），零业务写。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/agent_mind_page.dart';
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

final List<RequestOptions> calls = [];

Future<ResponseBody> handler(RequestOptions o) async {
  calls.add(o);
  if (o.path == '/api/v1/characters/11/agent-mind') {
    return reply({
      'reflection': {
        'content': 'SYNTH_REFLECTION',
        'created_at': '2026-11-24T02:00:00',
      },
      'memory_search': {
        'total': 3,
        'hit': 2,
        'miss': 1,
        'avg_latency_ms': 42,
        'recent': [
          {
            'route': 'memory_search',
            'query': 'SYNTH_QUERY_ONE',
            'hit_count': 2,
            'returned': 1,
            'latency_ms': 30,
            'created_at': '2026-11-24T01:00:00',
          },
        ],
      },
      'running_notes': {
        'identity': {
          'content': 'SYNTH_IDENTITY',
          'updated_at': '2026-11-20T03:00:00',
        },
        'pinned': [
          {
            'memory_type': 'user_info',
            'label': 'SYNTH_LABEL',
            'content': 'SYNTH_PINNED_NOTE',
            'updated_at': '2026-11-21T04:00:00',
          },
        ],
      },
      'tasks': [
        {
          'trigger': 'user_message',
          'goal': 'SYNTH_GOAL',
          'status': 'done',
          'created_at': '2026-11-24T05:00:00',
        },
      ],
      'tool_logs': [
        {
          'trigger': 'chat',
          'route': 'memory_search',
          'status': 'success',
          'status_raw': 'ok',
          'steps': '{"query":"SYNTH_STEP"}',
          'latency_ms': 12,
          'created_at': '2026-11-24T06:00:00',
        },
        {
          'trigger': 'scheduler',
          'route': 'proactive',
          'status': 'blocked',
          'status_raw': 'blocked',
          'steps': '{}',
          'latency_ms': 0,
          'created_at': '2026-11-24T06:10:00',
        },
      ],
      'scheduler_trace': {'skipped_recent': 2, 'window': 200},
      'mcp_calls': [
        {
          'server_name': 'SYNTH_MCP',
          'tool': 'lookup',
          'status': 'failed',
          'status_raw': 'error',
          'error': 'SYNTH_MCP_ERROR',
          'latency_ms': 5,
          'created_at': '2026-11-24T07:00:00',
        },
      ],
    });
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpPage(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1400, 2600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(
    home: AgentMindPage(
      client: client,
      characterId: 11,
      characterName: 'SYNTH_C',
      rs: const ReaderStrings('zh'),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  setUp(calls.clear);

  testWidgets('记忆检索：三张统计卡 + 明细 + 只 GET 一次', (tester) async {
    await pumpPage(tester);
    expect(find.text('2'), findsWidgets); // 命中
    expect(find.text('1'), findsWidgets); // 未命中
    expect(find.text('42ms'), findsOneWidget);
    expect(find.textContaining('SYNTH_QUERY_ONE'), findsOneWidget);
    expect(calls.length, 1);
    expect(calls.single.method, 'GET');
    expect(calls.single.path, '/api/v1/characters/11/agent-mind');
  });

  testWidgets('运行笔记：身份画像 + 置顶摘要', (tester) async {
    await pumpPage(tester);
    await tester.tap(find.byType(Tab).at(1));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_IDENTITY'), findsOneWidget);
    expect(find.textContaining('SYNTH_PINNED_NOTE'), findsOneWidget);
    expect(find.textContaining('SYNTH_LABEL'), findsOneWidget);
  });

  testWidgets('反思/任务：正文与目标原文展示', (tester) async {
    await pumpPage(tester);
    await tester.tap(find.byType(Tab).at(2));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_REFLECTION'), findsOneWidget);

    await tester.tap(find.byType(Tab).at(3));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_GOAL'), findsOneWidget);
    expect(find.text('done'), findsNothing); // 状态原文只在副标题，不单独放标题
  });

  testWidgets('工具日志：成功/拦截/未触发计数与 MCP 分区', (tester) async {
    await pumpPage(tester);
    await tester.tap(find.byType(Tab).at(4));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_STEP'), findsOneWidget);
    expect(find.textContaining('账号级 MCP 调用'), findsOneWidget);
    expect(find.textContaining('SYNTH_MCP_ERROR'), findsOneWidget);
    expect(find.textContaining('调度未触发 2'), findsOneWidget);
  });
}
