// AI 生活三 Tab 守卫（2026-11-24，对齐 App LifeHomeScreen）：
// 兴趣与目标合并加载（/life/interests + /life/goals，一次刷新两份），
// 产物库卡片带图片（相对路径补全 baseUrl），缺图不崩。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/life_pane.dart';
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
  if (o.path == '/api/v1/life/timeline') {
    return reply({
      'items': [
        {
          'id': 1,
          'sub_type': 'reflection',
          'content': 'SYNTH_LIFE_EVENT',
          'importance': 3,
          'created_at': '2026-11-20T10:00:00',
        }
      ],
    });
  }
  if (o.path == '/api/v1/life/interests') {
    return reply({
      'items': [
        {'id': 3, 'name': 'SYNTH_INTEREST', 'level': 72},
      ],
    });
  }
  if (o.path == '/api/v1/life/goals') {
    return reply({
      'items': [
        {
          'id': 5,
          'type': 'creative',
          'title': 'SYNTH_GOAL',
          'description': 'SYNTH_GOAL_DESC',
          'priority': 3,
          'progress': 2,
          'progress_total': 5,
          'status': 'active',
          'related_user': false,
        }
      ],
    });
  }
  if (o.path == '/api/v1/life/artifacts') {
    return reply({
      'items': [
        {
          'id': 9,
          'type': 'image',
          'title': 'SYNTH_ARTIFACT',
          'content_text': 'SYNTH_ART_TEXT',
          'content_url': '/uploads/synth.png',
          'created_at': '2026-11-21T03:00:00',
        }
      ],
    });
  }
  if (o.path == '/api/v1/life/browsing') {
    return reply({
      'items': [
        {
          'id': 4,
          'title': 'SYNTH_PAGE',
          'url': 'https://example.com/synth',
          'duration_sec': 95,
          'activity_type': 'learn',
          'created_at': '2026-11-22T08:00:00',
        }
      ],
    });
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpLife(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1200, 2200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final client = AmbApiClient(dio: Dio());
  client.configure(baseUrl: 'http://srv.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: LifePane(
        character: const Character(id: 11, name: 'SYNTH_C'),
        client: client,
        rs: const ReaderStrings('zh'),
        cs: const CoreStrings('zh'),
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  const rs = ReaderStrings('zh');

  testWidgets('三个 Tab：时间线 / 兴趣与目标 / 产物库', (tester) async {
    await pumpLife(tester);
    expect(find.text(rs.t('lifeTimeline')), findsOneWidget);
    expect(find.text(rs.t('lifeFocus')), findsOneWidget);
    expect(find.text(rs.t('lifeArtifacts')), findsOneWidget);
    expect(find.text(rs.t('lifeBrowsing')), findsOneWidget);
    expect(find.textContaining('SYNTH_LIFE_EVENT'), findsOneWidget);
  });

  testWidgets('浏览记录 Tab：标题/学习标签/时长', (tester) async {
    await pumpLife(tester);
    await tester.tap(find.text(rs.t('lifeBrowsing')));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_PAGE'), findsOneWidget);
    expect(find.text(rs.t('activityLearn')), findsOneWidget);
    expect(find.textContaining('2 ${rs.t('unitMin')}'), findsOneWidget);
  });

  testWidgets('兴趣与目标：两条列表同时到位，进度按 progress_total 展示', (tester) async {
    await pumpLife(tester);
    await tester.tap(find.text(rs.t('lifeFocus')));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_INTEREST'), findsOneWidget);
    expect(find.text('SYNTH_GOAL'), findsOneWidget);
    expect(find.text('SYNTH_GOAL_DESC'), findsOneWidget);
    expect(find.text('${rs.t('lifeGoalProgress')} 2/5'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
  });

  testWidgets('产物库：相对图片路径补全为服务器地址', (tester) async {
    await pumpLife(tester);
    await tester.tap(find.text(rs.t('lifeArtifacts')));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_ARTIFACT'), findsOneWidget);
    expect(find.text('SYNTH_ART_TEXT'), findsOneWidget);
    final image = tester.widget<Image>(find.byType(Image).first);
    final provider = image.image as NetworkImage;
    expect(provider.url, 'http://srv.local/uploads/synth.png');
  });
}
