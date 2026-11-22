// 批 8（2026-11-22）页面守卫：Life 首页状态只读渲染＋会话条内置搜索过滤。
// 全部合成数据（SYNTH_ 前缀）。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/chat/session_strip.dart';
import 'package:ambrace_pwa/features/reader/life_home_page.dart';
import 'package:ambrace_pwa/l10n/reader_strings.dart';
import 'package:ambrace_pwa/l10n/session_strings.dart';
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
  if (o.path == '/api/v1/life-home/state') {
    return reply({
      'character_id': 11,
      'character_name': 'SYN_HOME_CHAR',
      'current_room': 'library',
      'location': 'library',
      'rooms': [
        {'id': 'living', 'name': 'SYN_ROOM_LIVING'},
        {'id': 'library', 'name': 'SYN_ROOM_LIBRARY'},
      ],
      'world': null,
      'pets': [
        {'id': 1, 'name': 'SYN_PET'},
      ],
      'player': {'stamina': 22, 'mood': 65, 'hunger': 70},
      'ai': {'name': 'SYN_AI', 'current_status': 'SYN_AI_STATUS'},
    });
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpLifeHome(WidgetTester tester) async {
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(
    home: LifeHomePage(client: client, characterId: 11, rs: const ReaderStrings('zh')),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Life 首页状态：角色/位置/玩家三值/房间列表渲染', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await pumpLifeHome(tester);
    expect(find.textContaining('SYN_HOME_CHAR'), findsOneWidget);
    expect(find.textContaining('library'), findsWidgets);
    expect(find.textContaining('SYN_AI_STATUS'), findsOneWidget);
    expect(find.text('22'), findsOneWidget); // stamina
    expect(find.text('65'), findsOneWidget); // mood
    expect(find.text('SYN_ROOM_LIVING'), findsOneWidget);
    expect(find.textContaining('SYN_PET'), findsNothing); // pets 只计数
  });

  testWidgets('会话条搜索：图标开过滤框 → 标题过滤 + 计数变化', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
    client.dio.httpClientAdapter = FakeAdapter(handler);
    final sessions = [
      ChatSessionSummary(id: 1, characterId: 11, title: 'SYN_SESS_ALPHA'),
      ChatSessionSummary(id: 2, characterId: 11, title: 'SYN_SESS_BETA'),
    ];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SessionStrip(
          sessions: sessions,
          selectedId: 1,
          status: 'ready',
          errorText: '',
          creating: false,
          onSelect: (_) {},
          onCreate: () {},
          onRetry: () {},
          ss: const SessionStrings('zh'),
        ),
      ),
    ));
    await tester.pumpAndSettle();
    // 打开过滤框（搜索图标）
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'ALPHA');
    await tester.pumpAndSettle();
    expect(find.textContaining('SYN_SESS_ALPHA'), findsOneWidget);
    expect(find.textContaining('SYN_SESS_BETA'), findsNothing);
    expect(find.textContaining('没有匹配的会话'), findsNothing);
    // 清空过滤 → 全量恢复
    await tester.enterText(find.byType(TextField), '');
    await tester.pumpAndSettle();
    expect(find.textContaining('SYN_SESS_BETA'), findsOneWidget);
  });
}
