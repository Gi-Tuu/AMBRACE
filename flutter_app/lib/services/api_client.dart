import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import 'api_exception.dart';
import 'server_identity.dart';

export 'api/profile_api.dart';
export 'api/characters_api.dart';
export 'api/chat_api.dart';
export 'api/memories_api.dart';
export 'api/diary_api.dart';
export 'api/moments_api.dart';
export 'api/pets_api.dart';
export 'api/timeline_api.dart';
export 'api/system_api.dart';
export 'api/user_states_api.dart';
export 'api/user_content_api.dart';
export 'api/ai_chats_api.dart';
export 'api/privacy_api.dart';
export 'api/user_location_api.dart';
export 'api/plugins_api.dart';
export 'api/mcp_api.dart';
export 'api/weave_api.dart';
export 'api/platform_profiles_api.dart';
export 'api/chat_groups_api.dart';
export 'api/phone_workflows_api.dart';
export 'api/life_api.dart';
export 'api/life_home_api.dart';
export 'api/admin_api.dart';
export 'api/game_api.dart';
export 'api/llm_configs_api.dart';
export 'api/family_api.dart';

class ApiClient {
  static final ApiClient _instance = ApiClient._internal();
  factory ApiClient() => _instance;

  late final Dio _dio;
  String _baseUrl = "";
  String _token = "";

  /// 401 统一处理钩子（F7-c）：main.dart 启动时注入（清登录态 + 跳登录页）。
  /// 触发条件：401 + 已配置 token + 非认证端点 + 3 秒去重（并发 401 只处理一次）。
  void Function()? onUnauthorized;
  DateTime? _lastUnauthorizedAt;

  ApiClient._internal() {
    _dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 10),
    ));
    // 批 0-3 M0-b：身份拦截器必须在错误归一拦截器**之前**注册——
    // dio 的 onError 按注册顺序传播，先还原原始 JSON，存量 401/detail 提取才不受影响。
    _dio.interceptors.add(_IdentityInterceptor());
    // 统一错误归一（F7-c）：所有 DioException.error 挂 ApiException（分类+文案），
    // 异常抛出类型不变，存量 catch 兼容；401 按钩子处理（见 onUnauthorized）。
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

  /// 认证端点（登录/注册失败自带 401，不触发会话失效钩子）
  bool _isAuthEndpoint(String path) {
    // B4：按路径段精确匹配，避免 contains('/auth') 误豁免 authoritative 等路径
    final segments = Uri.tryParse(path.toLowerCase())?.pathSegments ?? const <String>[];
    bool seg(String s) => segments.contains(s);
    return seg('login') || seg('register') || seg('auth');
  }

  /// 领域 extension 方法访问用
  Dio get dio => _dio;

  /// Configure the singleton with server URL and optional token.
  /// Call this once at app startup and when settings change.
  void configure({required String baseUrl, String token = ""}) {
    _baseUrl = baseUrl;
    _dio.options.baseUrl = baseUrl;
    ApiClientBaseUrl.current = baseUrl;
    ServerIdentity.instance.kickLoad();
    // B4 修复（2026-09-01 审查）：以传入 token 为唯一准绳——非空就设置，空就彻底清头，
    // 避免登出/换号后 dio 单例残留上一个账号的 Authorization。
    if (token.isNotEmpty) {
      _setToken(token);
    } else {
      clearAuth();
    }
  }

  /// 清除认证头与内存 token（登出/换号时调用）
  void clearAuth() {
    _token = '';
    _dio.options.headers.remove('Authorization');
    _lastUnauthorizedAt = null;
  }

  String get baseUrl => _baseUrl;
  String get token => _token;

  Duration _serverOffset = Duration.zero;
  DateTime? _offsetFetchedAt;

  /// 已校准的服务器时钟偏移（服务器UTC - 本地UTC）
  /// 校准服务器时钟偏移（5 分钟复用；失败静默保留旧值，不阻塞业务）。
  /// 本地消息时间戳用它，避免手机与服务器时钟偏差导致气泡排序错乱。
  Future<void> ensureServerOffset() async {
    if (_offsetFetchedAt != null &&
        DateTime.now().difference(_offsetFetchedAt!) < const Duration(minutes: 5)) {
      return;
    }
    try {
      final r = await _dio.get('/api/v1/system/health',
          options: Options(connectTimeout: const Duration(seconds: 3), receiveTimeout: const Duration(seconds: 3)));
      final ts = (r.data as Map<String, dynamic>)['timestamp'] as String?;
      if (ts != null && ts.isNotEmpty) {
        final serverUtc = DateTime.parse(ts).toUtc();
        _serverOffset = serverUtc.difference(DateTime.now().toUtc());
        _offsetFetchedAt = DateTime.now();
      }
    } catch (_) {
      // 校准失败：保留上次偏移（首次为 0）
    }
  }

  /// 按服务器时钟返回当前 UTC 时间
  DateTime serverNow() => DateTime.now().toUtc().add(_serverOffset);

  void updateBaseUrl(String url) {
    _baseUrl = url;
    _dio.options.baseUrl = url;
    ApiClientBaseUrl.current = url;
    ServerIdentity.instance.kickLoad();
  }

  void _setToken(String token) {
    _token = token;
    _dio.options.headers["Authorization"] = "Bearer $token";
    // 会话切换（重新登录/登出）即重置 401 去重窗口
    _lastUnauthorizedAt = null;
  }

  /// AI 内心世界（Phase J/P1，2026-08-16）：最近复盘 + 任务记录 + 工具轨迹
  Future<Map<String, dynamic>> getAgentMind(int characterId) async {
    final r = await _dio.get('/api/v1/characters/$characterId/agent-mind');
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<List<Map<String, dynamic>>> getLorebook(int characterId) async {
    final r = await _dio.get('/api/v1/characters/$characterId/lorebook');
    return ((r.data as Map)['items'] as List? ?? []).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> createLorebook(int characterId,
      {required String title, required String content, required List<String> keywords,
       required List<String> excludeKeywords, bool active = true,
       bool isRegex = false, int probability = 100, String inclusionGroup = "",
       int stickyRounds = 0, int cooldownRounds = 0}) async {
    final r = await _dio.post('/api/v1/characters/$characterId/lorebook', data: {
      'title': title, 'content': content, 'keywords': keywords,
      'exclude_keywords': excludeKeywords, 'active': active,
      'is_regex': isRegex, 'probability': probability, 'inclusion_group': inclusionGroup,
      'sticky_rounds': stickyRounds, 'cooldown_rounds': cooldownRounds,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> updateLorebook(int characterId, int entryId,
      {required String title, required String content, required List<String> keywords,
       required List<String> excludeKeywords, bool active = true,
       bool isRegex = false, int probability = 100, String inclusionGroup = "",
       int stickyRounds = 0, int cooldownRounds = 0}) async {
    final r = await _dio.put('/api/v1/characters/$characterId/lorebook/$entryId', data: {
      'title': title, 'content': content, 'keywords': keywords,
      'exclude_keywords': excludeKeywords, 'active': active,
      'is_regex': isRegex, 'probability': probability, 'inclusion_group': inclusionGroup,
      'sticky_rounds': stickyRounds, 'cooldown_rounds': cooldownRounds,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<void> deleteLorebook(int characterId, int entryId) async {
    await _dio.delete('/api/v1/characters/$characterId/lorebook/$entryId');
  }

  Future<List<Map<String, dynamic>>> getWorldFacts(int characterId) async {
    final r = await _dio.get('/api/v1/characters/$characterId/world-facts');
    return ((r.data as Map)['items'] as List? ?? []).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> createWorldFact(int characterId, String content) async {
    final r = await _dio.post('/api/v1/characters/$characterId/world-facts',
        data: {'content': content, 'predicate': 'setting'});
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> updateWorldFact(
    int characterId,
    int factId, {
    required String content,
    String? predicate,
  }) async {
    final r = await _dio.put(
      '/api/v1/characters/$characterId/world-facts/$factId',
      data: {'content': content, if (predicate != null) 'predicate': predicate},
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<void> deleteWorldFact(int characterId, int factId) async {
    await _dio.delete('/api/v1/characters/$characterId/world-facts/$factId');
  }

  /// 事实「修正历史」只读（小增量 2026-09-16）：返回该事实槽的当前值 + 历史版本链。
  Future<Map<String, dynamic>> getWorldFactHistory(int characterId, int factId) async {
    final r = await _dio.get(
      '/api/v1/characters/$characterId/world-facts/$factId/history',
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 将后端返回的相对路径（如 /uploads/...）解析为完整 URL
  String resolveUrl(String? url) {
    if (url == null || url.isEmpty) return "";
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    return _baseUrl.replaceAll(RegExp(r'/+$'), '') + url;
  }
}

/// 解析分页列表响应：`data[key]` 缺失/非 List 时返回空列表（各领域 API 统一兜底）
List<T> parseListItems<T>(dynamic data, String key, T Function(dynamic) convert) {
  final items = (data as Map<String, dynamic>)[key] as List? ?? [];
  return items.map(convert).toList();
}

/// 批 0-3 M0-b：服务器身份固定 —— 请求带 nonce + 白名单响应验签。
///
/// 未配对 / 校验模式 `off` 时本拦截器对每个请求**不做任何事**（不加头、不改
/// responseType、不改响应体），既有行为逐字节不变。
///
/// 三态处置（模式由 [ServerIdentity.verifyMode] 决定）：
/// * `off`：不进本拦截器（[ServerIdentity.shouldGuard] 直接 false）；
/// * `shadow`：验签但只累计 [ServerIdentity.mismatchCount] / 调试日志，响应照常放行；
/// * `enforce`：**签名不符 / 格式非法**时拒绝该响应（抛 `ApiException(kind: 'identity')`）。
///   响应**没有签名**（[VerifyOutcome.unsigned]）在 enforce 下同样只记录不拒绝——
///   服务器 `identity_enforce_mode` 默认 off 且白名单外的端点一律不出签，
///   拒绝未签名会把整个 App 打死（方案 §4.7「旧包永不被拒」的对称面）。
class _IdentityInterceptor extends Interceptor {
  static const String _nonceKey = 'ambrace_identity_nonce';

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    final identity = ServerIdentity.instance;
    try {
      if (!identity.isLoaded) {
        // 只「kick」一次异步加载，绝不在拦截器里 await：
        // 本轮按未配对处理（零行为变化），下一次请求起生效。
        identity.kickLoad();
        handler.next(options);
        return;
      }
      if (!identity.shouldGuard(options.path)) {
        handler.next(options);
        return;
      }
      final nonce = ServerIdentity.newNonce();
      options.headers[ServerIdentity.challengeHeader] = nonce;
      options.extra[_nonceKey] = nonce;
      // 验签摘要必须打在服务器返回的原始字节上：json 模式拿不到原文，
      // 故先按纯文本取回，验签后在 onResponse 里还原成调用方期望的 Map。
      options.responseType = ResponseType.plain;
    } catch (_) {
      // 任何异常（prefs 不可用等）都不得影响既有请求
    }
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    final identity = ServerIdentity.instance;
    final nonce = response.requestOptions.extra[_nonceKey];
    if (nonce is! String) {
      handler.next(response);
      return;
    }
    final data = response.data;
    if (data is! String) {
      // 非文本响应（bytes/stream 等）拿不到原文，摘要无从比对：跳过，不误判为伪造
      handler.next(response);
      return;
    }
    final status = response.statusCode ?? 200;
    final proof = response.headers.value(ServerIdentity.proofHeader);
    final outcome = identity.verify(
        nonce: nonce, status: status, bodyText: data, proof: proof);
    _restoreJsonBody(response, data);
    _log(identity, outcome, response.requestOptions.path, status);
    final rejected = _rejectFor(identity, outcome, response, status);
    if (rejected != null) {
      handler.reject(rejected);
      return;
    }
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    final identity = ServerIdentity.instance;
    final nonce = err.requestOptions.extra[_nonceKey];
    if (nonce is! String) {
      handler.next(err);
      return;
    }
    final response = err.response;
    if (response != null) {
      final data = response.data;
      if (data is String) {
        final status = response.statusCode ?? 0;
        final outcome = identity.verify(
            nonce: nonce,
            status: status,
            bodyText: data,
            proof: response.headers.value(ServerIdentity.proofHeader));
        _restoreJsonBody(response, data);
        _log(identity, outcome, err.requestOptions.path, status);
      }
    }
    // 错误响应一律保留原始语义（401 钩子/detail 文案不能被验签顶掉）
    handler.next(err);
  }

  /// enforce 档下的拒绝异常；不拒绝时返回 null
  static DioException? _rejectFor(ServerIdentity identity, VerifyOutcome outcome,
      Response response, int status) {
    if (identity.verifyMode != 'enforce') return null;
    if (outcome != VerifyOutcome.mismatch && outcome != VerifyOutcome.malformed) {
      return null;
    }
    return DioException(
      requestOptions: response.requestOptions,
      response: response,
      type: DioExceptionType.badResponse,
      error: ApiException(
          kind: 'identity', statusCode: status, message: '服务器身份校验失败'),
    );
  }

  /// 把被强制为 plain 的响应体还原成 JSON（非 JSON 时保持字符串，同 dio 的 json 兜底）
  static void _restoreJsonBody(Response response, String? text) {
    if (text == null) return;
    try {
      response.data = jsonDecode(text);
    } catch (_) {
      response.data = text;
    }
  }

  static void _log(ServerIdentity identity, VerifyOutcome outcome, String path,
      int status) {
    if (outcome == VerifyOutcome.ok) return;
    if (kDebugMode) {
      debugPrint(
          '[identity] $outcome $path status=$status '
          'verified=${identity.verifiedCount} mismatch=${identity.mismatchCount} '
          'unsigned=${identity.unsignedCount}');
    }
  }
}
