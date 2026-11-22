// 时光轴页（2026-11-22 批 5）Widget 守卫：认识天数/首次聊天/条目渲染。合成数据。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/timeline_page.dart';
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
  if (o.path == '/api/v1/timeline/11') {
    return reply({
      'character_id': 11,
      'character_name': 'SYN',
      'days_known': 110,
      'first_chat_at': '2026-08-05T06:11:50',
      'has_milestones': false,
      'items': [
        {'date': '2026-11-22', 'type': 'memory', 'title': 'SYN_TL_TITLE',
         'desc': 'SYN_TL_DESC', 'importance': 118},
      ],
    });
  }
  return reply({'detail': 'not_found'}, 404);
}

void main() {
  testWidgets('时光轴：认识天数/首次聊天/条目渲染', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
    client.dio.httpClientAdapter = FakeAdapter(handler);
    await tester.pumpWidget(MaterialApp(
      home: TimelinePage(client: client, characterId: 11, rs: const ReaderStrings('zh')),
    ));
    await tester.pumpAndSettle();
    expect(find.textContaining('110'), findsOneWidget); // 认识天数
    expect(find.textContaining('2026-08-05'), findsOneWidget); // 首次聊天
    expect(find.textContaining('SYN_TL_TITLE'), findsOneWidget);
    expect(find.textContaining('SYN_TL_DESC'), findsOneWidget);
  });
}
