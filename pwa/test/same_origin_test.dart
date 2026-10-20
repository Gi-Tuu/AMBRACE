// 同源默认地址守卫（M1a-1，2026-10-16）：默认 base 来自页面 **origin**（同源），
// 不再写死 `http://127.0.0.1:18080`；取不到 origin 的 VM 环境留空且可注入；
// 显示的服务端地址与实际请求 baseUrl 不分叉。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_pwa/main.dart' as app;
import 'package:ambrace_pwa/platform/browser_origin.dart';
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

ResponseBody _reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

void main() {
  group('pageOrigin（纯函数，不依赖机器环境）', () {
    test('同源 origin 只取 scheme+host+port，不含 /pwa/ 子路径', () {
      expect(pageOrigin(Uri.parse('http://127.0.0.1:8099/pwa/')),
          'http://127.0.0.1:8099');
      expect(pageOrigin(Uri.parse('http://127.0.0.1:8099/pwa/index.html')),
          'http://127.0.0.1:8099');
    });

    test('https 默认端口不带端口；显式端口保留', () {
      expect(pageOrigin(Uri.parse('https://example.com/pwa/')), 'https://example.com');
      expect(pageOrigin(Uri.parse('https://example.com:8443/x')),
          'https://example.com:8443');
    });

    test('IPv6 origin 保留必要方括号并丢弃userinfo', () {
      expect(pageOrigin(Uri.parse('http://[::1]:8099/pwa/')), 'http://[::1]:8099');
      expect(pageOrigin(Uri.parse('https://sample:secret@example.com/pwa/')), 'https://example.com');
    });

    test('非 http(s) 或没有 host ⇒ null（不伪造、不依赖 file: 路径）', () {
      expect(pageOrigin(Uri.parse('file:///d:/repo/pwa/build/web/index.html')), isNull);
      expect(pageOrigin(Uri.parse('about:blank')), isNull);
      expect(pageOrigin(Uri.parse('http:///no-host-here')), isNull);
    });
  });

  group('WebShellEnv 默认地址', () {
    test('可注入 origin 作为默认地址', () {
      final env = app.WebShellEnv(defaultBaseUrl: 'http://127.0.0.1:8099');
      expect(env.defaultBase, 'http://127.0.0.1:8099');
    });

    test('未注入且无浏览器 origin ⇒ 留空，不再写死本机 18080', () {
      final env = app.WebShellEnv();
      expect(env.defaultBase.contains('18080'), isFalse,
          reason: '默认地址仍写死旧合成 mock 端口');
      expect(env.defaultBase, isNot(contains('127.0.0.1:18080')));
    });
  });

  group('壳接线', () {
    testWidgets('注入 origin：登录表单预填，且请求 baseUrl 与显示一致（不分叉）', (tester) async {
      const base = 'http://127.0.0.1:8099';
      await tester.pumpWidget(const app.AmbracePwaApp(defaultBaseUrl: base));
      await tester.pumpAndSettle();

      final serverField = tester.widget<TextField>(find.byType(TextField).first);
      expect(serverField.controller!.text, base);

      final dynamic state = tester.state(find.byType(app.WorkspacePage));
      expect(state.env.client.dio.options.baseUrl, base,
          reason: '请求地址与显示地址分叉');
    });

    testWidgets('VM 未注入 ⇒ 服务器地址留空（不写死、不读机器 file: 路径）', (tester) async {
      await tester.pumpWidget(const app.AmbracePwaApp());
      await tester.pumpAndSettle();
      final serverField = tester.widget<TextField>(find.byType(TextField).first);
      expect(serverField.controller!.text, '');
    });

    testWidgets('登录与角色请求实际都打到注入的同源 origin', (tester) async {
      const base = 'http://127.0.0.1:8099';
      final seen = <String>[];
      await tester.pumpWidget(const app.AmbracePwaApp(defaultBaseUrl: base));
      await tester.pumpAndSettle();

      final dynamic state = tester.state(find.byType(app.WorkspacePage));
      state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
        seen.add(o.baseUrl);
        if (o.path.endsWith('/login')) return _reply({'access_token': 'T'});
        return _reply({'characters': []});
      });

      await tester.enterText(find.byType(TextField).at(1), 'u');
      await tester.enterText(find.byType(TextField).at(2), 'p');
      await tester.tap(find.text('登录'));
      await tester.pumpAndSettle();

      expect(seen, isNotEmpty);
      expect(seen.every((b) => b == base), isTrue,
          reason: '出现非注入 origin 的请求：$seen');
    });
  });
}
