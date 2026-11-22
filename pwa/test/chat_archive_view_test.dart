// 会话归档视图（2026-11-22 批 3）Widget 守卫：年/月/日分组渲染＋客户端内容过滤。
// 全部合成数据（SYNTH_ 前缀）。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/chat/archive_view.dart';
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
  if (o.path == '/api/v1/chat/sessions/5/archive') {
    return reply({
      'session_id': 5,
      'days': [
        {
          'date': '2026-11-01',
          'count': 2,
          'messages': [
            {'id': 1, 'sender_type': 'user', 'content': 'SYNTH_ALPHA_HELLO',
             'image_url': null, 'created_at': '2026-11-01T10:00:00'},
            {'id': 2, 'sender_type': 'ai', 'content': 'SYNTH_BETA_REPLY',
             'image_url': null, 'created_at': '2026-11-01T10:01:00'},
          ],
        },
        {
          'date': '2026-10-15',
          'count': 1,
          'messages': [
            {'id': 3, 'sender_type': 'ai', 'content': 'SYNTH_GAMMA_OLD',
             'image_url': null, 'created_at': '2026-10-15T09:00:00'},
          ],
        },
      ],
      'total_days': 2,
    });
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpPage(WidgetTester tester) async {
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  final t = ChatTransport(client);
  await tester.pumpWidget(MaterialApp(
    home: ArchiveViewPage(
        sessionId: 5, transport: t, rs: const ReaderStrings('zh')),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('归档渲染：年/月/日分组＋消息行', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await pumpPage(tester);
    expect(find.text('2026'), findsOneWidget); // 年分组
    expect(find.text('2026-11'), findsOneWidget); // 月分组
    expect(find.text('2026-11-01'), findsOneWidget); // 日卡片
    expect(find.text('2026-10-15'), findsOneWidget);
    expect(find.textContaining('SYNTH_ALPHA_HELLO'), findsOneWidget);
    expect(find.textContaining('SYNTH_GAMMA_OLD'), findsOneWidget);
  });

  testWidgets('客户端内容过滤：只留匹配日与消息', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await pumpPage(tester);
    await tester.enterText(find.byType(TextField), 'ALPHA');
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_ALPHA_HELLO'), findsOneWidget);
    expect(find.textContaining('SYNTH_BETA_REPLY'), findsNothing);
    expect(find.text('2026-10-15'), findsNothing); // 无匹配的日整组滤除
    expect(find.textContaining('没有匹配的消息'), findsNothing);
  });

  testWidgets('过滤无匹配 → 提示', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await pumpPage(tester);
    await tester.enterText(find.byType(TextField), 'NO_SUCH_THING');
    await tester.pumpAndSettle();
    expect(find.text('没有匹配的消息。'), findsOneWidget);
  });
}
