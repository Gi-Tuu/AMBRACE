import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

/// 公共 Dio 测试桩（B3 从 B2 的私有 _FakeAdapter 提升）：
/// 按「METHOD + path」注册固定 JSON 响应，不产生真实网络请求。
///
/// 用法：
/// ```dart
/// final adapter = FakeApiAdapter();
/// ApiClient().dio.httpClientAdapter = adapter;
/// adapter.json('GET', '/api/v1/chat/sessions/7/messages', {'messages': [...]});
/// ```
class FakeApiAdapter implements HttpClientAdapter {
  final Map<String, ResponseBody Function(RequestOptions options)> _handlers = {};

  /// 注册精确匹配（method + path，忽略 query）的 JSON 响应。
  void json(String method, String path, Object? data, {int status = 200}) {
    _handlers[_key(method, path)] = (_) => body(data, status);
  }

  /// 注册自定义处理器（可按 options 动态返回）。
  void handle(String method, String path,
      ResponseBody Function(RequestOptions options) handler) {
    _handlers[_key(method, path)] = handler;
  }

  /// 未匹配一律 404（业务层按异常静默处理）。
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final handler = _handlers[_key(options.method, options.uri.path)];
    if (handler != null) return handler(options);
    return body({'detail': 'not found'}, 404);
  }

  @override
  void close({bool force = false}) {}

  String _key(String method, String path) =>
      '${method.toUpperCase()} ${path.replaceAll(RegExp(r'/+$'), '')}';

  /// 构造 JSON ResponseBody。
  static ResponseBody body(Object? data, [int status = 200]) =>
      ResponseBody.fromString(
        jsonEncode(data),
        status,
        headers: {
          Headers.contentTypeHeader: [Headers.jsonContentType],
        },
      );
}
