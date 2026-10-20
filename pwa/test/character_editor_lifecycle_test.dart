// PWA M1a-5 · 角色编辑表单的**页面级**长期守卫。
//
// 与主审 `four_widget_probes.dart` 同断言（390×844＋键盘 260 可达保存/取消、保存中全禁用、
// 连点两次只 1 次写、真实弹窗返回给「继续编辑/放弃」）。harness 要点：
// - 真实 `CharacterEditorSheet.show()` 弹窗（Back 才能被 PopScope 拦到）；
// - 键盘用 `tester.view.viewInsets = FakeViewPadding(bottom: 260)` 模拟；
// - 滚动要用**按钮的 Scrollable 祖先**（`find.ancestor`），不能用 TextField 内部的；
// - 不靠大画布绕过窄屏：这里就是 390×844。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/character_management/character_editor_sheet.dart';
import 'package:ambrace_pwa/l10n/character_editor_strings.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.handler);
  final Future<ResponseBody> Function(RequestOptions options) handler;
  final List<RequestOptions> requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) {
    requests.add(options);
    return handler(options);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(Object body, [int status = 200]) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: <String, List<String>>{
        Headers.contentTypeHeader: <String>[Headers.jsonContentType],
      },
    );

const Map<String, dynamic> _charJson = <String, dynamic>{
  'id': 7,
  'user_id': 3,
  'name': 'Neo',
  'bio': 'lives downtown',
  'personality': 'gentle',
  'chat_style': 'casual',
  'greeting_message': 'hi',
  'gender': 'female',
  'appearance': 'short hair',
  'talkativeness': 60,
  'talkativeness_locked': false,
  'timezone_offset': 8,
};

void main() {
  Future<_Adapter> open(WidgetTester t,
      Future<ResponseBody> Function(RequestOptions) handle,
      {bool keyboard = false}) async {
    t.view.physicalSize = const Size(390, 844);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    addTearDown(t.view.resetViewInsets);
    final dio = Dio(BaseOptions(baseUrl: 'http://unit.invalid'));
    final a = _Adapter(handle);
    dio.httpClientAdapter = a;
    await t.pumpWidget(MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: FilledButton(
              onPressed: () => CharacterEditorSheet.show(
                context,
                client: AmbApiClient(dio: dio),
                strings: const CharacterEditorStrings('en'),
                authGen: 1,
                routeGen: 1,
                isStale: (a, r) => false,
                currentUserId: 3,
                onSaved: (_) {},
              ),
              child: const Text('SYNTH_OPEN'),
            ),
          ),
        ),
      ),
    ));
    await t.tap(find.text('SYNTH_OPEN'));
    await t.pumpAndSettle();
    expect(find.byType(CharacterEditorSheet), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(9));
    await t.enterText(find.byType(TextField).first, 'Neo');
    await t.pump();
    if (keyboard) {
      t.view.viewInsets = const FakeViewPadding(bottom: 260);
      await t.pump();
    }
    return a;
  }

  Future<void> reveal(WidgetTester t) async {
    final button = find.widgetWithText(FilledButton, 'Create');
    final outer =
        find.ancestor(of: button, matching: find.byType(Scrollable)).first;
    await t.scrollUntilVisible(button, 180, scrollable: outer, maxScrolls: 40);
    await t.pumpAndSettle();
  }

  testWidgets('390x844 keyboard can reach create and cancel', (t) async {
    await open(t, (_) async => _json(_charJson, 201), keyboard: true);
    await reveal(t);
    final create = find.widgetWithText(FilledButton, 'Create');
    final cancel = find.widgetWithText(OutlinedButton, 'Cancel');
    expect(t.getRect(create).bottom, lessThanOrEqualTo(844 - 260));
    expect(t.getRect(cancel).bottom, lessThanOrEqualTo(844 - 260));
    expect(create.hitTestable(), findsOneWidget);
    expect(cancel.hitTestable(), findsOneWidget);
  });

  testWidgets('saving disables every field and switch', (t) async {
    final pending = Completer<ResponseBody>();
    final a = await open(t, (_) => pending.future);
    await reveal(t);
    await t.tap(find.widgetWithText(FilledButton, 'Create'));
    await t.pumpAndSettle();
    expect(a.requests.where((o) => o.method == 'POST').length, 1);
    for (final field in t.widgetList<TextField>(find.byType(TextField))) {
      expect(field.enabled, isFalse);
    }
    expect(t.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged, isNull);
    expect(
        t.widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Cancel')).onPressed,
        isNull);
    pending.complete(_json(_charJson, 201));
    await t.pumpAndSettle();
    expect(find.byType(CharacterEditorSheet), findsNothing);
  });

  testWidgets('two rapid save taps send one write', (t) async {
    final pending = Completer<ResponseBody>();
    final a = await open(t, (_) => pending.future);
    await reveal(t);
    final save = find.widgetWithText(FilledButton, 'Create');
    await t.tap(save);
    await t.tap(save);
    await t.pumpAndSettle();
    expect(a.requests.where((o) => o.method == 'POST').length, 1);
    pending.complete(_json(_charJson, 201));
    await t.pumpAndSettle();
  });

  testWidgets('real modal Back offers keep and discard', (t) async {
    await open(t, (_) async => _json(_charJson, 201));
    await t.binding.handlePopRoute();
    await t.pumpAndSettle();
    expect(find.text('Discard unsaved changes?'), findsOneWidget);
    await t.tap(find.text('Keep editing'));
    await t.pumpAndSettle();
    expect(find.byType(CharacterEditorSheet), findsOneWidget);
    await t.binding.handlePopRoute();
    await t.pumpAndSettle();
    expect(find.text('Discard unsaved changes?'), findsOneWidget);
    await t.tap(find.text('Discard'));
    await t.pumpAndSettle();
    expect(find.byType(CharacterEditorSheet), findsNothing);
  });
}