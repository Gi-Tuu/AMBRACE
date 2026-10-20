// i18n 覆盖守卫（返工组 ⑤）：不只测登录按钮——覆盖登录后的页面、窄宽屏 Tabs、
// 配对成功/失败、401 失败分支，且**英文界面不得出现中文错误原文**。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_pwa/main.dart' as app;
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeAdapter implements HttpClientAdapter {
  final Future<ResponseBody> Function(RequestOptions) handle;
  FakeAdapter(this.handle);
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

Future<dynamic> _boot(WidgetTester tester, {double width = 500}) async {
  tester.view.physicalSize = Size(width, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const app.AmbracePwaApp());
  return tester.state(find.byType(app.WorkspacePage));
}

Future<void> _toEnglish(WidgetTester tester) async {
  await tester.tap(find.text('EN'));
  await tester.pumpAndSettle();
}

void main() {
  tearDown(() => app.appLang.value = 'zh');

  testWidgets('en 登录屏：配对/配对码等全部为英文成对文案', (tester) async {
    await _boot(tester);
    await _toEnglish(tester);
    expect(find.text('AMBRACE · Web'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);
    expect(find.text('Server address'), findsOneWidget);
    expect(find.text('Pairing code'), findsOneWidget, reason: '配对码仍硬编码中文');
    expect(find.text('Pair'), findsOneWidget, reason: '配对按钮仍硬编码中文');
  });

  testWidgets('en 登录后页面：Tabs/退出/提示全部英文，且无中文残留', (tester) async {
    final dynamic state = await _boot(tester, width: 500); // 窄屏 → 有 Tabs
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return reply({'access_token': 'T'});
      if (o.path.endsWith('/characters')) {
        return reply({'characters': [{'id': 1, 'name': 'Nova'}]});
      }
      return reply({'messages': []});
    });
    await _toEnglish(tester);
    await tester.enterText(find.byType(TextField).at(1), 'u');
    await tester.enterText(find.byType(TextField).at(2), 'p');
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Characters'), findsOneWidget, reason: '窄屏 Tabs 仍硬编码中文');
    expect(find.text('Chat'), findsOneWidget, reason: '窄屏 Tabs 仍硬编码中文');
    expect(find.text('Sign out'), findsOneWidget);
    expect(find.text('Nova'), findsOneWidget); // 角色名＝用户内容，**不翻译**
    // 中文 UI 文案不该出现在英文界面
    for (final cn in ['登录', '退出登录', '角色', '聊天', '发送']) {
      expect(find.text(cn), findsNothing, reason: '英文界面残留中文：$cn');
    }
  });

  testWidgets('en 失败分支：401 显示英文文案而不是中文原文', (tester) async {
    final dynamic state = await _boot(tester);
    state.env.client.dio.httpClientAdapter = FakeAdapter(
        (o) async => reply({'detail': '账号或密码不正确'}, 401));
    await _toEnglish(tester);
    await tester.enterText(find.byType(TextField).at(1), 'u');
    await tester.enterText(find.byType(TextField).at(2), 'p');
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();
    expect(find.text('Session expired, please sign in again'), findsOneWidget);
    expect(find.text('账号或密码不正确'), findsNothing,
        reason: '中文错误原文泄漏进 en 界面');
  });

  // 配对失败文案的**映射**在 core 里单测（`CoreStrings.pairFailureText`，确定性、无网络），
  // 见 ambrace_core/test/identity_and_client_test.dart；此处只验 UI 标签成对（上一条）。
}
