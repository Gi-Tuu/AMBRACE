// 角色八维状态页（2026-11-24）：GET /characters/{id}/states → 蛛网图 + 八维数值条。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/character_status_page.dart';
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

ResponseBody reply(Object data) => ResponseBody.fromString(jsonEncode(data), 200,
    headers: {Headers.contentTypeHeader: ['application/json']});

void main() {
  const rs = ReaderStrings('zh');

  testWidgets('八维数值与信任/更新时间渲染', (tester) async {
    tester.view.physicalSize = const Size(900, 1800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final client = AmbApiClient();
    client.configure(baseUrl: 'http://srv.local', token: 'SYNTH');
    client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/characters/11/states') {
        return reply({
          'character_id': 11,
          'mood': 61,
          'body_temp': 55,
          'desire': 40,
          'possessiveness': 70,
          'fatigue': 88,
          'sensitivity': 30,
          'comfort': 66,
          'anger': 12,
          'trust': 70,
          'updated_at': '2026-11-22T08:00:00',
        });
      }
      return reply({'detail': 'nf'});
    });
    await tester.pumpWidget(MaterialApp(
      home: CharacterStatusPage(
        client: client,
        characterId: 11,
        characterName: 'SYNTH_C',
        rs: rs,
        cs: const CoreStrings('zh'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text(rs.t('stateFatigue')), findsOneWidget);
    expect(find.text('88'), findsOneWidget);
    expect(find.text('${rs.t('stateTrust')} 70'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNWidgets(8));
  });
}
