// 角色详情入口接线守卫（2026-11-24）：设置 / 内心世界 / 世界设定三入口，
// 且附区逐项容错（relationship 失败不再拖垮 lorebook/agent-mind 等块）。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/profile_pane.dart';
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

Future<ResponseBody> handler(RequestOptions o) async {
  if (o.path == '/api/v1/characters/11') {
    return reply({
      'id': 11,
      'name': 'SYNTH_C',
      'cognitive_loop_enabled': true,
      'created_at': '2026-01-01T00:00:00',
    });
  }
  if (o.path == '/api/v1/relationships') {
    // 故意失败：验证逐项容错（旧实现会让后面全部为空）
    return reply({'detail': 'boom'}, 500);
  }
  if (o.path == '/api/v1/characters/11/world-facts') return reply({'items': []});
  if (o.path == '/api/v1/characters/11/lorebook') return reply({'items': []});
  if (o.path == '/api/v1/characters/11/state-history') {
    return reply({'character_id': 11, 'points': []});
  }
  if (o.path == '/api/v1/characters/11/agent-mind') {
    return reply({
      'reflection': {'content': 'SYNTH_REFLECTION'},
      'tasks': [],
      'tool_logs': [],
    });
  }
  if (o.path == '/api/v1/scheduler/settings/11') {
    return reply({'character_id': 11, 'diary_enabled': false});
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpProfile(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1400, 2600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: ProfilePane(
        character: const Character(id: 11, name: 'SYNTH_C'),
        client: client,
        editor: EditorApi(client),
        rs: const ReaderStrings('zh'),
        cs: const CoreStrings('zh'),
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('三入口存在；关系失败不拖垮内心摘要与世界计数', (tester) async {
    await pumpProfile(tester);
    const rs = ReaderStrings('zh');
    expect(find.text(rs.t('openSettings')), findsOneWidget);
    expect(find.text(rs.t('openAgentMind')), findsWidgets);
    expect(find.text(rs.t('worldSettings')), findsOneWidget);
    expect(find.textContaining('SYNTH_REFLECTION'), findsOneWidget);
    expect(
        find.text('${rs.t('lorebook')} 0 · ${rs.t('worldFacts')} 0'),
        findsOneWidget);
    // 「TA 的日常」三条入口（AI 生活 / 角色日记 / 记忆本），对齐 App 角色详情行
    expect(find.text(rs.t('taDaily')), findsOneWidget);
    expect(find.text(rs.t('life')), findsOneWidget);
    expect(find.text(rs.t('modCharDiary')), findsOneWidget);
    expect(find.text(rs.t('memoryBook')), findsOneWidget);
  });

  testWidgets('点角色设置进入设置页（并带 editor 打开世界设定）', (tester) async {
    await pumpProfile(tester);
    const rs = ReaderStrings('zh');
    await tester.tap(find.widgetWithText(OutlinedButton, rs.t('openSettings')));
    await tester.pumpAndSettle();
    expect(find.textContaining(rs.t('csTitle')), findsOneWidget);
    expect(find.text(rs.t('csGroupDaily')), findsOneWidget);
  });
}
