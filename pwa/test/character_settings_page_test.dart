// PWA「角色设置」页守卫（2026-11-24）：全分组渲染、父子级联、单字段 PUT 契约。
// 全部合成数据（SYNTH_ 前缀）；断言写入 body 只含被点的那一个字段。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/character_settings_page.dart';
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

final List<RequestOptions> writes = [];

Map<String, dynamic> settingsJson() => {
      'character_id': 11,
      'enable_proactive': true,
      'frequency': 'medium',
      'max_daily_proactive': 3,
      'birthday_enabled': false,
      'holiday_enabled': false,
      'diary_enabled': false,
      'moments_enabled': true,
      'state_trigger_enabled': true,
      'memory_review_enabled': true,
      'cold_war_enabled': true,
      'mood_badge_enabled': true,
      'image_gen_enabled': true,
      'active_image_gen_enabled': false,
      'privacy_enabled': true,
      'privacy_lock_enabled': true,
      'reasoning_level': 1,
      'show_tools_enabled': false,
      'moments_comment_enabled': true,
      'weave_full_inject_enabled': false,
      'dnd_enabled': true,
      'dnd_start': '01:30',
      'dnd_end': '07:00',
      'check_in_enabled': false,
      'control_enabled': false,
      'life_enabled': true,
      'life_intensity': 'high',
      'life_share_enabled': true,
    };

Future<ResponseBody> handler(RequestOptions o) async {
  if (o.method == 'GET' && o.path == '/api/v1/scheduler/settings/11') {
    return reply(settingsJson());
  }
  if (o.method == 'GET' && o.path == '/api/v1/characters/11') {
    return reply({
      'id': 11,
      'name': 'SYNTH_C',
      'cognitive_loop_enabled': true,
      'created_at': '2026-01-01T00:00:00',
    });
  }
  if (o.method == 'PUT' && o.path == '/api/v1/scheduler/settings/11') {
    writes.add(o);
    return reply({'status': 'ok'});
  }
  if (o.method == 'PUT' && o.path == '/api/v1/characters/11') {
    writes.add(o);
    return reply({'id': 11, 'name': 'SYNTH_C'});
  }
  return reply({'detail': 'not_found'}, 404);
}

Future<void> pumpPage(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1500, 3400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final client = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  client.dio.httpClientAdapter = FakeAdapter(handler);
  await tester.pumpWidget(MaterialApp(
    home: CharacterSettingsPage(
      client: client,
      editor: EditorApi(client),
      characterId: 11,
      characterName: 'SYNTH_C',
      rs: const ReaderStrings('zh'),
    ),
  ));
  await tester.pumpAndSettle();
}

SwitchListTile _tile(WidgetTester tester, String label) =>
    tester.widget<SwitchListTile>(
        find.widgetWithText(SwitchListTile, label).first);

void main() {
  setUp(writes.clear);

  testWidgets('全分组渲染：七组标题 + 关键字段值', (tester) async {
    await pumpPage(tester);
    const rs = ReaderStrings('zh');
    expect(find.text(rs.t('csGroupDaily')), findsOneWidget);
    expect(find.text(rs.t('csGroupCreation')), findsOneWidget);
    expect(_tile(tester, rs.t('csDiary')).value, isFalse);
    expect(find.textContaining('01:30'), findsWidgets);
    expect(find.textContaining('07:00'), findsWidgets);
    await tester.scrollUntilVisible(find.text(rs.t('csCognitiveLoop')), 200,
        scrollable: find.byType(Scrollable).first);
    expect(_tile(tester, rs.t('csCognitiveLoop')).value, isTrue);
    for (final g in ['csGroupPrivacy', 'csGroupStatus',
        'csGroupDebug', 'csGroupWorld']) {
      await tester.scrollUntilVisible(find.text(rs.t(g)), 200,
          scrollable: find.byType(Scrollable).first);
      expect(find.text(rs.t(g)), findsOneWidget);
    }
  });

  testWidgets('单字段 PUT：点日记只写 diary_enabled，成功提示', (tester) async {
    await pumpPage(tester);
    const rs = ReaderStrings('zh');
    await tester.tap(find.widgetWithText(SwitchListTile, rs.t('csDiary')));
    await tester.pumpAndSettle();
    expect(writes.single.method, 'PUT');
    expect(writes.single.data, {'diary_enabled': true});
    expect(find.text(rs.t('csSaved')), findsOneWidget);
  });

  testWidgets('父子级联：关掉线上生活后，分享与强度不可改', (tester) async {
    await pumpPage(tester);
    const rs = ReaderStrings('zh');
    await tester.tap(find.widgetWithText(SwitchListTile, rs.t('csOfflineLife')));
    await tester.pumpAndSettle();
    expect(writes.single.data, {'life_enabled': false});
    expect(_tile(tester, rs.t('csLifeShare')).onChanged, isNull);
    // 子项点击不产生新写入
    writes.clear();
    await tester.tap(find.widgetWithText(SwitchListTile, rs.t('csLifeShare')));
    await tester.pumpAndSettle();
    expect(writes, isEmpty);
  });

  testWidgets('思考挡位：点「深度思考」只写 reasoning_level=2', (tester) async {
    await pumpPage(tester);
    const rs = ReaderStrings('zh');
    await tester.scrollUntilVisible(find.text(rs.t('csReasoningDeep')), 200,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text(rs.t('csReasoningDeep')));
    await tester.pumpAndSettle();
    expect(writes.single.data, {'reasoning_level': 2});
  });

  testWidgets('认知循环走角色接口（不是 scheduler 设置）', (tester) async {
    await pumpPage(tester);
    const rs = ReaderStrings('zh');
    writes.clear();
    await tester.scrollUntilVisible(
        find.widgetWithText(SwitchListTile, rs.t('csCognitiveLoop')), 200,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(
        find.widgetWithText(SwitchListTile, rs.t('csCognitiveLoop')));
    await tester.pumpAndSettle();
    expect(writes.single.path, '/api/v1/characters/11');
    expect(writes.single.data, {'cognitive_loop_enabled': false});
  });
}
