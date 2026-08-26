import 'package:dio/dio.dart';

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

class ApiClient {
  static final ApiClient _instance = ApiClient._internal();
  factory ApiClient() => _instance;

  late final Dio _dio;
  String _baseUrl = "";
  String _token = "";

  ApiClient._internal() {
    _dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 10),
    ));
  }

  /// 领域 extension 方法访问用
  Dio get dio => _dio;

  /// Configure the singleton with server URL and optional token.
  /// Call this once at app startup and when settings change.
  void configure({required String baseUrl, String token = ""}) {
    _baseUrl = baseUrl;
    _dio.options.baseUrl = baseUrl;
    if (token.isNotEmpty) {
      _setToken(token);
    }
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
  }

  void _setToken(String token) {
    _token = token;
    _dio.options.headers["Authorization"] = "Bearer $token";
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

  Future<void> deleteWorldFact(int characterId, int factId) async {
    await _dio.delete('/api/v1/characters/$characterId/world-facts/$factId');
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
