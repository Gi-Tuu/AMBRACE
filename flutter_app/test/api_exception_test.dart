import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/services/api_exception.dart';

/// F7-c-0a 统一 API 异常测试：拦截器归一分类（network/timeout/server/client/unauthorized）、
/// detail 提取、messageOf、401 钩子（已登录触发 / 认证端点豁免 / 3s 去重）。
class _ScriptedAdapter implements HttpClientAdapter {
  final HttpClientAdapter _inner = Dio().httpClientAdapter;
  final ResponseBody Function(RequestOptions options) handler;
  _ScriptedAdapter(this.handler);

  @override
  void close({bool force = false}) => _inner.close(force: force);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    return handler(options);
  }
}

ResponseBody _json(int code, Map<String, dynamic> body) => ResponseBody(
      Stream.value(Uint8List.fromList(utf8.encode(jsonEncode(body)))),
      code,
      headers: {Headers.contentTypeHeader: ['application/json']},
    );

void _install(ResponseBody Function(RequestOptions) handler) {
  ApiClient().dio.httpClientAdapter = _ScriptedAdapter(handler);
  ApiClient().configure(baseUrl: 'http://127.0.1:9', token: 'tok');
}

void _reset() {
  ApiClient().onUnauthorized = null;
  ApiClient().configure(baseUrl: 'http://127.0.1:9', token: '');
}

void main() {
  setUp(_reset);
  tearDown(_reset);

  test('messageOf：DioException 归一 / ApiException 直取 / 普通字符串', () {
    const api = ApiException(kind: 'server', statusCode: 500, message: '服务器开小差了');
    expect(ApiException.messageOf(api), '服务器开小差了');
    expect(ApiException.messageOf(StateError('boom')), contains('boom'));
    expect(ApiException.messageOf(null), '未知错误');
  });

  test('拦截器归一：5xx → server + detail 提取', () async {
    _install((o) => _json(500, {'detail': '数据库忙'}));
    try {
      await ApiClient().dio.get('/api/v1/x');
      fail('should throw');
    } on DioException catch (e) {
      final api = e.error;
      expect(api, isA<ApiException>());
      final a = api as ApiException;
      expect(a.kind, 'server');
      expect(a.statusCode, 500);
      expect(a.message, '数据库忙');
      expect(ApiException.messageOf(e), '数据库忙');
    }
  });

  test('拦截器归一：断网 → network（中文文案）', () async {
    _install((o) => throw DioException.connectionError(
          requestOptions: o,
          reason: 'refused',
          error: StateError('x'),
        ));
    try {
      await ApiClient().dio.get('/api/v1/x');
      fail('should throw');
    } on DioException catch (e) {
      final a = e.error as ApiException;
      expect(a.kind, 'network');
      expect(a.statusCode, isNull);
      expect(a.message, contains('网络连接失败'));
    }
  });

  test('拦截器归一：超时 → timeout；4xx → client', () async {
    _install((o) => throw DioException(
          requestOptions: o,
          type: DioExceptionType.receiveTimeout,
        ));
    try {
      await ApiClient().dio.get('/api/v1/x');
    } on DioException catch (e) {
      expect((e.error as ApiException).kind, 'timeout');
    }
    _install((o) => _json(403, {}));
    try {
      await ApiClient().dio.get('/api/v1/x');
    } on DioException catch (e) {
      final a = e.error as ApiException;
      expect(a.kind, 'client');
      expect(a.statusCode, 403);
    }
  });

  test('401 钩子：已登录 + 非认证端点 → 触发一次', () async {
    _install((o) => _json(401, {}));
    var fired = 0;
    ApiClient().onUnauthorized = () => fired++;
    try {
      await ApiClient().dio.get('/api/v1/characters/1');
    } on DioException catch (e) {
      expect((e.error as ApiException).kind, 'unauthorized');
    }
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(fired, 1);
  });

  test('401 钩子：认证端点豁免（登录失败不触发）', () async {
    _install((o) => _json(401, {}));
    var fired = 0;
    ApiClient().onUnauthorized = () => fired++;
    try {
      await ApiClient().dio.post('/api/v1/auth/login');
    } on DioException catch (_) {}
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(fired, 0);
  });

  test('401 钩子：3 秒去重（并发 401 只触发一次）', () async {
    _install((o) => _json(401, {}));
    var fired = 0;
    ApiClient().onUnauthorized = () => fired++;
    await Future.wait([
      ApiClient().dio.get('/api/v1/a').catchError((_) => Response(requestOptions: RequestOptions(path: ''))),
      ApiClient().dio.get('/api/v1/b').catchError((_) => Response(requestOptions: RequestOptions(path: ''))),
      ApiClient().dio.get('/api/v1/c').catchError((_) => Response(requestOptions: RequestOptions(path: ''))),
    ]);
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(fired, 1);
  });
}
