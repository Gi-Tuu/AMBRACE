// 并发隔离守卫（返工组 ③）：直接采用主审的反例形态（A 慢 B 快、迟到旧结果），
// 并补「退出途中 / 多次点击 / 销毁页面」分支。修前应至少有一条能复现旧断言失败。
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

/// 建一个可注入假适配器的薄壳；返回 State 以便注入与断言。
Future<dynamic> _boot(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const app.AmbracePwaApp());
  final dynamic state = tester.state(find.byType(app.WorkspacePage));
  return state;
}

Future<void> _login(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).at(1), 'synthetic_user');
  await tester.enterText(find.byType(TextField).at(2), 'synthetic_password');
  await tester.tap(find.text('登录'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('正对照：无竞争的 B 历史正常显示', (tester) async {
    final dynamic state = await _boot(tester);
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
      if (o.path.endsWith('/characters')) {
        return reply({'characters': [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}]});
      }
      if (o.path == '/api/v1/chat/sessions') {
        // 工作台新增：GET 为账号会话列表；POST 为「默认复用」建会话
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        return reply({'id': int.parse('${o.queryParameters['character_id']}')});
      }
      if (o.path.endsWith('/messages')) {
        final sid = int.parse(o.path.split('/')[5]);
        return reply({'session_id': sid, 'total': 1, 'messages': [
          {'id': sid, 'session_id': sid, 'sender_type': 'ai',
           'content': sid == 1 ? 'SYNTH_A_HISTORY' : 'SYNTH_B_HISTORY',
           'created_at': '2026-10-16T01:00:00Z'}
        ]});
      }
      throw StateError('Unexpected endpoint ${o.path}');
    });
    await _login(tester);
    await tester.tap(find.text('B'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget);
    expect(find.text('SYNTH_A_HISTORY'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('反例：A 慢 B 快 —— A 的迟到结果不得覆盖已选中的 B', (tester) async {
    final dynamic state = await _boot(tester);
    final aSlow = Completer<ResponseBody>();
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
      if (o.path.endsWith('/characters')) {
        return reply({'characters': [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}]});
      }
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        final cid = o.queryParameters['character_id'];
        if (cid == 1) return aSlow.future; // A 的建会话一直挂着
        return reply({'id': 2});
      }
      if (o.path.endsWith('/messages')) {
        final sid = int.parse(o.path.split('/')[5]);
        return reply({'session_id': sid, 'total': 1, 'messages': [
          {'id': sid, 'session_id': sid, 'sender_type': 'ai',
           'content': sid == 1 ? 'SYNTH_A_HISTORY' : 'SYNTH_B_HISTORY',
           'created_at': '2026-10-16T01:00:00Z'}
        ]});
      }
      throw StateError('Unexpected endpoint ${o.path}');
    });
    await _login(tester);

    await tester.tap(find.text('A')); // A 开始（挂起）
    await tester.pump();
    await tester.tap(find.text('B')); // 立刻切到 B
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget);

    aSlow.complete(reply({'id': 1})); // A 的旧请求此刻才返回
    await tester.pumpAndSettle();

    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget, reason: 'B 历史被 A 覆盖了');
    expect(find.text('SYNTH_A_HISTORY'), findsNothing, reason: '迟到旧结果流入新会话');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('退出途中：旧请求迟到不得恢复旧身份/旧历史', (tester) async {
    final dynamic state = await _boot(tester);
    final late = Completer<ResponseBody>();
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
      if (o.path.endsWith('/characters')) {
        return reply({'characters': [{'id': 1, 'name': 'A'}]});
      }
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        return late.future; // 建会话挂起
      }
      if (o.path.endsWith('/messages')) {
        return reply({'session_id': 1, 'messages': [], 'total': 0});
      }
      throw StateError('Unexpected endpoint ${o.path}');
    });
    await _login(tester);
    await tester.tap(find.text('A'));
    await tester.pump();
    await tester.tap(find.text('退出登录')); // 退出（代次作废 + 真实取消）
    await tester.pumpAndSettle();

    late.complete(reply({'id': 1})); // 旧请求迟到
    await tester.pumpAndSettle();

    expect(find.text('登录'), findsOneWidget, reason: '退出后又被旧结果拉回登录态之外');
    expect(find.text('SYNTH_A_HISTORY'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('销毁页面：在飞请求迟到不抛异常、不写状态', (tester) async {
    final dynamic state = await _boot(tester);
    final late = Completer<ResponseBody>();
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
      if (o.path.endsWith('/characters')) {
        return reply({'characters': [{'id': 1, 'name': 'A'}]});
      }
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        return late.future;
      }
      return reply({'session_id': 1, 'messages': [], 'total': 0});
    });
    await _login(tester);
    await tester.tap(find.text('A'));
    await tester.pump();
    await tester.pumpWidget(const SizedBox()); // 销毁
    late.complete(reply({'id': 1}));
    await tester.pumpAndSettle();
    // 走到这里即说明没有「对已销毁 State 调 setState」的异常
    expect(tester.takeException(), isNull);
  });
}
