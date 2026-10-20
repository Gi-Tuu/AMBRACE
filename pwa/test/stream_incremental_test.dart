// 增量流可见性守卫（返工组 ②）：证明「HTTP 未结束时首 delta 已可见」的交付机制——
// 用可控 ResponseBody 流模拟 SSE：先推一个 delta，**保持流未关闭**，断言气泡已出现；
// 再推 block/done 收尾。若某处把整包缓冲后才交付，本测试会在「流未关」那一步失败。
//
// 说明：本测试验证**传输→解析→渲染**的增量语义（Dart 层）；Web 端 XHR 适配器是否真增量
// 必须另以浏览器集成检查验证；本测试不替代真实 XHR 的网络行为验证。
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

ResponseBody json(Map<String, dynamic> data, [int status = 200]) =>
    ResponseBody.fromString(jsonEncode(data), status,
        headers: {Headers.contentTypeHeader: ['application/json']});

const String partial = 'SYNTH_PARTIAL_DELTA';
const String full = 'SYNTH_FULL_REPLY';

void main() {
  testWidgets('SSE 流未结束时首 delta 已渲染（不是等整包才显示）', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final sse = StreamController<Uint8List>();
    await tester.pumpWidget(const app.AmbracePwaApp());
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/login')) return json({'access_token': 'T'});
      if (o.path.endsWith('/characters')) {
        return json({'characters': [{'id': 1, 'name': 'A'}]});
      }
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return json({'sessions': [], 'total': 0});
        return json({'id': 1});
      }
      if (o.path == '/api/v1/auth/profile') return json({'id': 1});
      if (o.path.endsWith('/messages') && o.method == 'GET') {
        return json({'session_id': 1, 'messages': [], 'total': 0});
      }
      if (o.path.endsWith('/messages/stream')) {
        // 返回**尚未关闭**的流：内容由测试逐步推入
        return ResponseBody(sse.stream, 200,
            headers: {Headers.contentTypeHeader: ['text/event-stream']});
      }
      throw StateError('Unexpected ${o.method} ${o.path}');
    });

    // 登录 + 选角色
    await tester.enterText(find.byType(TextField).at(1), 'u');
    await tester.enterText(find.byType(TextField).at(2), 'p');
    await tester.tap(find.text('登录'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).last, '你好');
    await tester.tap(find.text('发送'));
    await tester.pump(); // 让请求发起、订阅建立

    // 只推第一个 delta —— 流仍然开着
    sse.add(utf8.encode('data: ${jsonEncode({'type': 'delta', 'text': partial})}\n\n'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 20));

    expect(find.textContaining(partial), findsOneWidget,
        reason: '流未结束却看不到首 delta ⇒ 仍是缓冲式交付');

    // 推 block + done 收尾（桥接纯文本行 → 需 \n\n 分隔的 SSE 帧）
    sse.add(utf8.encode('data: ${jsonEncode({'type': 'block', 'id': 2, 'content': full})}\n\n'));
    sse.add(utf8.encode('data: ${jsonEncode({'type': 'done'})}\n\n'));
    await sse.close();
    await tester.pumpAndSettle();

    expect(find.text(full), findsOneWidget);
    expect(find.textContaining(partial), findsNothing, reason: 'done 后未清打字机缓冲');
    await tester.pumpWidget(const SizedBox());
  });
}
