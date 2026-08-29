import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/screens/character/character_settings_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P5 角色设置测试：
/// 玻璃 AppBar、六分组 AuroraCard、开关 PUT 写回、展开子项、导航行。
void main() {
  late FakeApiAdapter api;
  late _CapturingAdapter capture;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    capture = _CapturingAdapter();
    api = capture;
    ApiClient().dio.httpClientAdapter = capture;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    api.json('GET', '/api/v1/scheduler/settings/1', {
      'enable_proactive': true,
      'memory_review_enabled': true,
      'diary_enabled': true,
      'moments_enabled': true,
      'moments_comment_enabled': true,
      'state_trigger_enabled': true,
      'cold_war_enabled': true,
      'mood_badge_enabled': true,
      'image_gen_enabled': false,
      'privacy_enabled': true,
      'privacy_lock_enabled': true,
      'reasoning_level': 0,
      'life_enabled': true,
      'life_share_enabled': true,
      'life_intensity': 'low',
      'frequency': 'medium',
      'dnd_enabled': false,
      'check_in_enabled': false,
    });
    api.json('GET', '/api/v1/characters/1', {
      'id': 1,
      'name': 'Alpha',
      'cognitive_loop_enabled': false,
    });
    api.json('PUT', '/api/v1/scheduler/settings/1', {});
  });

  Widget app() => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: const CharacterSettingsScreen(characterId: 1, characterName: 'Alpha'),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(CharacterSettingsScreen)))!;

  testWidgets('renders AppBar + six group titles + AuroraCard groups',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.charSettings), findsOneWidget);
    // 分组标题随滚动逐段懒加载：滚动全程累计收集
    final titles = [
      l10n.dailyGroup,
      l10n.creationGroup,
      l10n.worldGroup,
      l10n.socialGroup,
      l10n.privacyGroup,
      l10n.statusGroup,
    ];
    final seen = <String>{};
    // 首屏可见组先收集（拖动后顶部组会被懒卸载）
    for (final t in titles) {
      if (tester.any(find.text(t))) seen.add(t);
    }
    for (var i = 0; i < 20 && seen.length < titles.length; i++) {
      await tester.drag(find.byType(ListView), const Offset(0, -400));
      await tester.pump();
      for (final t in titles) {
        if (tester.any(find.text(t))) seen.add(t);
      }
    }
    expect(seen, containsAll(titles));
    expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
  });

  testWidgets('tapping a switch row PUTs the field and updates UI',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    // 日常组第一行：AI 日记（点击行切换）
    await tester.tap(find.text(l10n.aiDiary));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    final body = capture.bodies['PUT /api/v1/scheduler/settings/1'];
    expect(body, isNotNull, reason: 'PUT should be called');
    expect(body!, contains('diary_enabled'));
    expect(body, contains('false'));
    // UI 更新：Switch 关闭（组内 SwitchListTile 的 value 反转）；
    // 这里通过再次点击恢复 true 验证状态机
    await tester.tap(find.text(l10n.aiDiary));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    final body2 = capture.bodies['PUT /api/v1/scheduler/settings/1'];
    expect(body2!, contains('true'));
  });

  testWidgets('expansion switch: tapping aiOfflineLife reveals children',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    // 展开前子项不可见
    expect(find.text(l10n.lifeShare), findsNothing);

    await tester.tap(find.text(l10n.aiOfflineLife));
    await tester.pumpAndSettle();

    // 子项出现：自然分享开关 + 生活强度 SegmentedButton
    expect(find.text(l10n.lifeShare), findsOneWidget);
    expect(find.text(l10n.lifeIntensity), findsOneWidget);
    expect(find.byType(SegmentedButton<String>), findsOneWidget);
  });

  testWidgets('world group nav rows exist', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    for (var i = 0; i < 20; i++) {
      await tester.drag(find.byType(ListView), const Offset(0, -400));
      await tester.pump();
      if (tester.any(find.text(l10n.lorebookTitle)) &&
          tester.any(find.text(l10n.worldFactsTitle))) {
        break;
      }
    }
    expect(find.text(l10n.lorebookTitle), findsOneWidget);
    expect(find.text(l10n.worldFactsTitle), findsOneWidget);
  });
}

/// 捕获 PUT/POST body 的适配器（同 character_edit_screen_test 的方案）。
class _CapturingAdapter extends FakeApiAdapter {
  final Map<String, String> bodies = {};

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (requestStream != null) {
      final bytes = <int>[];
      await for (final chunk in requestStream) {
        bytes.addAll(chunk);
      }
      bodies['${options.method} ${options.uri.path}'] = utf8.decode(bytes);
      return super.fetch(
          options, Stream<Uint8List>.value(Uint8List.fromList(bytes)), cancelFuture);
    }
    if (options.method != 'GET' && options.data != null) {
      final d = options.data;
      bodies['${options.method} ${options.uri.path}'] =
          d is String ? d : jsonEncode(d);
    }
    return super.fetch(options, requestStream, cancelFuture);
  }
}
