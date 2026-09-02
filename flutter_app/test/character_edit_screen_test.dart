import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/features/character/character_edit_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P5 角色创建/编辑测试：
/// 玻璃 AppBar（保存入口迁到底部）、四分组 AuroraCard、底部保存触发 POST、
/// 编辑态删除确认框、声音组渲染。
void main() {
  late FakeApiAdapter api;
  late _CapturingAdapter capture;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    // 捕获型适配器（继承 FakeApiAdapter，mock 直接注册在它自身上）
    capture = _CapturingAdapter();
    api = capture;
    ApiClient().dio.httpClientAdapter = capture;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    api.json('GET', '/api/v1/llm-configs', {'items': []});
    api.json('POST', '/api/v1/characters', {
      'id': 99,
      'name': 'Nova',
      'character_name': 'Nova',
      'sender_type': 'ai',
    });
  });

  Widget app({AICharacter? character}) => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: CharacterEditScreen(character: character),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(CharacterEditScreen)))!;

  /// 滚动到列表底部（保存按钮在懒加载区外，需滚动后才会构建）
  Future<void> scrollToSave(WidgetTester tester) async {
    final saveText = l10nOf(tester).save;
    for (var i = 0; i < 15; i++) {
      await tester.drag(find.byType(ListView), const Offset(0, -400));
      await tester.pump();
      if (tester.any(find.text(saveText))) {
        // 元素已构建 → ensureVisible 精确滚入视口（拖动累积可能未达 max extent）
        await tester.ensureVisible(find.text(saveText));
        await tester.pumpAndSettle();
        return;
      }
    }
    await tester.pumpAndSettle();
  }

  testWidgets('create mode: title/avatar/four groups/bottom save; AppBar has no save text',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.createFriend), findsOneWidget);
    final appBar = tester.widget<AppBar>(find.byType(AppBar));
    expect(appBar.actions ?? const <Widget>[], isEmpty);
    // 头像提示
    expect(find.text(l10n.tapToPickAvatar), findsOneWidget);
    // 分组标题随滚动逐段懒加载：滚动全程累计收集
    final titles = [l10n.basicInfo, l10n.personalityGroup, l10n.model, l10n.voiceGroup];
    final seen = <String>{};
    for (var i = 0; i < 15; i++) {
      await tester.drag(find.byType(ListView), const Offset(0, -400));
      await tester.pump();
      for (final t in titles) {
        if (tester.any(find.text(t))) seen.add(t);
      }
      if (tester.any(find.text(l10n.save))) break;
    }
    expect(seen, containsAll(titles));
    // AuroraCard 分组容器
    expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
    // 底部保存按钮（入口迁移底部；懒加载区外需滚动）
    await tester.ensureVisible(find.text(l10n.save));
    await tester.pumpAndSettle();
    expect(find.text(l10n.save), findsOneWidget);
    expect(find.byType(FilledButton), findsOneWidget);
  });

  testWidgets('bottom save: name input -> POST /characters called with name',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await tester.enterText(find.byType(TextFormField).first, 'Nova');
    await tester.pump();
    await scrollToSave(tester);
    await tester.tap(find.text(l10n.save));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump(const Duration(milliseconds: 300));
    final body = capture.bodies['POST /api/v1/characters'];
    expect(body, isNotNull, reason: 'POST /api/v1/characters should be called');
    expect(body!, contains('"name"'));
    expect(body, contains('Nova'));

    // 创建成功 → 问候语询问弹窗 → 跳过 → pop
    //（底部按钮 spinner 在 _saving 下持续旋转，pumpAndSettle 永不收敛 → 固定 pump）
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.text(l10n.generateGreetingAsk), findsOneWidget);
    await tester.tap(find.text(l10n.generateGreetingSkip));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('edit mode: editFriend title + delete button + confirm dialog',
      (tester) async {
    final c = AICharacter(id: 1, name: 'Alpha');
    await tester.pumpWidget(app(character: c));
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.editFriend), findsOneWidget);
    // 底部保存也在（创建/编辑都显示）；删除按钮在保存下方，滚动到可见
    await scrollToSave(tester);
    expect(find.text(l10n.deleteFriend), findsOneWidget);
    expect(find.byType(FilledButton), findsOneWidget);

    await tester.tap(find.text(l10n.deleteFriend));
    await tester.pumpAndSettle();
    expect(find.text(l10n.confirmDelete), findsOneWidget);
    // 取消关闭
    await tester.tap(find.text(l10n.cancel));
    await tester.pumpAndSettle();
    expect(find.text(l10n.confirmDelete), findsNothing);
  });

  testWidgets('voice group: label/rate/pitch sliders/preview row render',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    for (var i = 0; i < 15; i++) {
      await tester.drag(find.byType(ListView), const Offset(0, -400));
      await tester.pump();
      if (tester.any(find.text(l10n.voiceGroup))) break;
    }
    await tester.pumpAndSettle();

    expect(find.text(l10n.voiceLabel), findsWidgets); // 与分组标题同文案「声音」
    expect(find.text(l10n.voiceRate), findsOneWidget);
    expect(find.text(l10n.voicePitch), findsOneWidget);
    expect(find.text(l10n.previewVoice), findsOneWidget);
    // 不点试听
    expect(find.byType(Slider), findsNWidgets(3)); // 语速/语调/话痨度
  });
}

/// 捕获 POST body 的适配器（读 requestStream 后重新灌回，供上层 handler 处理）。
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
    // 兜底：requestStream 为空但带 data 时从 options 捕获
    if (options.method != 'GET' && options.data != null) {
      final d = options.data;
      bodies['${options.method} ${options.uri.path}'] =
          d is String ? d : jsonEncode(d);
    }
    return super.fetch(options, requestStream, cancelFuture);
  }
}
