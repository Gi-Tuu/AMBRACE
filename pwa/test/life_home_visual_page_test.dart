// 小家像素房间页（2026-11-24）：/life-home/state 渲染房间与家具；生活动作 POST 事件；
// 素材走 App 的 life_visual 资产（相对格坐标换算）。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/life_home_visual_page.dart';
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

  testWidgets('房间/家具/宠物/生活动作渲染，点「吃饭」发 POST', (tester) async {
    tester.view.physicalSize = const Size(1200, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final posted = <String>[];
    final client = AmbApiClient();
    client.configure(baseUrl: 'http://srv.local', token: 'SYNTH');
    client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/life-home/state') {
        return reply({
          'character_id': 11,
          'character_name': 'SYNTH_C',
          'current_room': 'living',
          'player': {'stamina': 70, 'mood': 50, 'hunger': 60},
          'rooms': [
            {
              'id': 'living',
              'name': '客厅',
              'furniture': [
                {
                  'key': 'sofa',
                  'name': '沙发',
                  'gx': 6,
                  'gy': 1,
                  'gw': 2,
                  'gh': 1,
                  'rotation': 0,
                }
              ],
            },
            {'id': 'bedroom', 'name': '卧室', 'furniture': []},
          ],
          'pets': [
            {'id': 3, 'name': 'SYNTH_PET', 'species': 'cat', 'hunger': 60, 'mood': 50, 'energy': 70},
          ],
        });
      }
      if (o.path == '/api/v1/life-home/event') {
        posted.add((o.data as Map)['action'] as String);
        return reply({
          'ok': true,
          'action': 'eat',
          'player': {'stamina': 72, 'mood': 55, 'hunger': 95},
        });
      }
      return reply({'detail': 'nf'});
    });
    await tester.pumpWidget(MaterialApp(
      home: LifeHomeVisualPage(
        client: client,
        character: const Character(id: 11, name: 'SYNTH_C'),
        rs: rs,
        cs: const CoreStrings('zh'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('客厅'), findsOneWidget);
    expect(find.text('卧室'), findsOneWidget);
    expect(find.textContaining('SYNTH_PET'), findsOneWidget);
    expect(find.text(rs.t('homeSleep')), findsOneWidget);
    await tester.tap(find.text(rs.t('homeEat')));
    await tester.pumpAndSettle();
    expect(posted, ['eat']);
    expect(find.textContaining('${rs.t('homeStamina')} 72'), findsOneWidget);
  });
}
