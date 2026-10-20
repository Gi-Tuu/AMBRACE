import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';

import 'api_exception.dart';
import 'identity.dart';
import 'xhr_stream_adapter.dart';

/// 跨端共享 API 客户端（唯一权威实现；手机壳与 Web 壳共用）。
///
/// 与手机 App 的 `ApiClient` 语义对齐：configure(baseUrl/token)、认证头、401 统一钩子、
/// 错误归一为 [ApiException]、身份响应验签（enforce 档拒绝伪造响应）。但**不** export
/// 任何移动端领域 API（文件上传/隐私/插件等），Web 壳导入它不会拖入原生实现。
class AmbApiClient {
  late final Dio _dio;
  final AmbIdentityVerifier identity;
  String _baseUrl = '';
  String _token = '';
  DateTime? _lastUnauthorizedAt;

  /// 401 统一处理钩子（壳启动时注入：清登录态 + 跳登录页）。
  void Function()? onUnauthorized;

  AmbApiClient({AmbIdentityVerifier? identity, Dio? dio})
      : identity = identity ?? AmbIdentityVerifier() {
    _dio = dio ??
        Dio(BaseOptions(
          connectTimeout: const Duration(seconds: 5),
          receiveTimeout: const Duration(seconds: 10),
        ));
    // Web 上换成**增量**适配器（边收边交付）；非 Web 返回 null ⇒ 保持 dio 默认（真流式本来就是）。
    final incremental = createIncrementalStreamAdapter();
    if (incremental != null) {
      _dio.httpClientAdapter = incremental;
    }
    _dio.interceptors.add(_IdentityInterceptor(this));
    _dio.interceptors.add(InterceptorsWrapper(
      onError: (e, handler) {
        final err = e.error;
        if (err is ApiException && err.kind == 'identity') {
          handler.next(e); // 验签失败的语义不再被二次归类
          return;
        }
        final api = ApiException.fromDio(e);
        if (api.kind == 'unauthorized' &&
            _token.isNotEmpty &&
            onUnauthorized != null &&
            !_isAuthEndpoint(e.requestOptions.path)) {
          final now = DateTime.now();
          if (_lastUnauthorizedAt == null ||
              now.difference(_lastUnauthorizedAt!) > const Duration(seconds: 3)) {
            _lastUnauthorizedAt = now;
            scheduleMicrotask(() => onUnauthorized!());
          }
        }
        handler.next(e.copyWith(error: api));
      },
    ));
  }

  bool _isAuthEndpoint(String path) {
    final segments = Uri.tryParse(path.toLowerCase())?.pathSegments ?? const <String>[];
    bool seg(String s) => segments.contains(s);
    return seg('login') || seg('register') || seg('auth');
  }

  Dio get dio => _dio;
  String get baseUrl => _baseUrl;
  String get token => _token;

  void configure({required String baseUrl, String token = ''}) {
    _baseUrl = baseUrl;
    _dio.options.baseUrl = baseUrl;
    identity.currentBaseUrl = baseUrl;
    identity.kickLoad();
    if (token.isNotEmpty) {
      _setToken(token);
    } else {
      clearAuth();
    }
  }

  void _setToken(String token) {
    _token = token;
    _dio.options.headers['Authorization'] = 'Bearer $token';
  }

  void clearAuth() {
    _token = '';
    _dio.options.headers.remove('Authorization');
    _lastUnauthorizedAt = null;
  }
}

/// 身份验签拦截器。
///
/// 只对 `AmbIdentity.signPaths` 生效，且仅在已配对 + 非 off 档时（未配对时零行为变化）。
/// 受管请求以 `ResponseType.plain` 取**原文**参与验签（JSON 重新编码会改摘要），
/// 验签通过后再本地解码为 JSON；enforce 档不符则拒绝该响应。
class _IdentityInterceptor extends Interceptor {
  _IdentityInterceptor(this._client);

  final AmbApiClient _client;
  static const String _nonceKey = '_ambrace_nonce';

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    if (_client.identity.shouldGuard(options.path)) {
      final nonce = AmbIdentity.newNonce();
      options.headers[AmbIdentity.challengeHeader] = nonce;
      options.extra[_nonceKey] = nonce;
      options.responseType = ResponseType.plain; // 拿原文验签
    }
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    final nonce = response.requestOptions.extra[_nonceKey];
    if (nonce is! String) {
      handler.next(response);
      return;
    }
    final body = response.data is String ? response.data as String : '';
    final proof = response.headers.value(AmbIdentity.proofHeader);
    final outcome = _client.identity.verify(
      nonce: nonce,
      status: response.statusCode ?? 0,
      bodyText: body,
      proof: proof,
    );
    if (outcome == VerifyOutcome.mismatch || outcome == VerifyOutcome.malformed) {
      // enforce 档拒绝；shadow 档只记录（照常放行，供观测）
      if (_client.identity.verifyMode == 'enforce') {
        handler.reject(DioException(
          requestOptions: response.requestOptions,
          response: response,
          type: DioExceptionType.badResponse,
          error: ApiException(
            kind: 'identity',
            statusCode: response.statusCode,
            message: '服务器身份校验不通过，已拒绝该响应',
          ),
        ));
        return;
      }
    }
    // 验签通过（或 shadow 放行）：把原文解码成 JSON，供领域代码按既有类型消费
    if (body.trim().isNotEmpty) {
      try {
        response.data = jsonDecode(body);
      } catch (_) {
        // 非 JSON（如纯文本健康检查）保持原文
      }
    }
    handler.next(response);
  }
}
