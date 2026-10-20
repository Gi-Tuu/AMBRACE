import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';

import '../models/character.dart';
import '../models/chat_message.dart';
import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import 'session_models.dart';

/// 解析一行 SSE `data:` 负载 → 事件 Map；非 data 行 / 空负载 / 非法 JSON 返回 null。
///
/// 与手机端同一口径（`type: user_message|delta|block|done|error|cold_war`）。
Map<String, dynamic>? parseSseDataLine(String line) {
  final l = line.trimRight();
  if (!l.startsWith('data:')) return null;
  final payload = l.substring(5).trim();
  if (payload.isEmpty) return null;
  if (payload == '[DONE]') return const {'type': 'done'};
  try {
    final v = jsonDecode(payload);
    return v is Map<String, dynamic> ? v : null;
  } catch (_) {
    return null;
  }
}

/// 聊天/登录/角色/会话的最小传输层（跨端共享）。
///
/// 只承载本切片闭环所必需的方法；上传/语音/图片等移动相关能力**不在此**（留在手机壳）。
class ChatTransport {
  ChatTransport(this.client);

  final AmbApiClient client;

  /// 登录 → 返回 token（服务器用 `X-Ambrace-Proof` 签名时由拦截器验签）。
  Future<String> login(String username, String password, {CancelToken? cancelToken}) async {
    final r = await client.dio.post(
      '/api/v1/auth/login',
      data: {'username': username, 'password': password},
      cancelToken: cancelToken,
    );
    final data = r.data as Map<String, dynamic>;
    final token = data['access_token'] ?? data['token'];
    if (token is! String || token.isEmpty) {
      throw StateError('login response missing token');
    }
    return token;
  }

  Future<List<Character>> getCharacters({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/characters', cancelToken: cancelToken);
    final data = r.data;
    final list = (data is Map ? data['characters'] : data) as List? ?? const [];
    return list
        .whereType<Map<String, dynamic>>()
        .map(Character.fromJson)
        .toList();
  }

  Future<int> createSession(int characterId,
      {CancelToken? cancelToken, bool forceNew = false}) async {
    final r = await client.dio.post(
      '/api/v1/chat/sessions',
      // force_new **只在显式新建时**下发（true）；默认请求与既有逐字节一致
      // （后端 `force_new` 默认 False ⇒ 复用最新活跃会话，旧行为不变）。
      queryParameters: {'character_id': characterId, if (forceNew) 'force_new': true},
      cancelToken: cancelToken,
    );
    final data = r.data as Map<String, dynamic>;
    return (data['id'] as num).toInt();
  }

  /// 当前账号的活跃会话列表（`GET /api/v1/chat/sessions`）。
  /// 接口**没有**角色过滤/排序/分页 query —— 按 character_id 筛选在客户端做，不伪造。
  Future<List<ChatSessionSummary>> listSessions({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/chat/sessions', cancelToken: cancelToken);
    final data = r.data;
    final list = (data is Map ? data['sessions'] : data) as List? ?? const [];
    try {
      return [
        for (final s in list)
          if (s is Map) ChatSessionSummary.fromJson(s.cast<String, dynamic>()),
      ];
    } on FormatException catch (e) {
      throw ApiException(kind: 'unknown', message: 'malformed chat sessions', cause: e);
    }
  }

  /// 历史分页（携带 `total`）：服务端按 `created_at DESC, id DESC` 取窗、再反转为
  /// **窗口内时间正序**；[skip]/[limit] 是**原始窗口**游标（limit 1..200）。
  Future<ChatHistoryPage> getMessagePage(
    int sessionId, {
    int skip = 0,
    int limit = 50,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.get(
      '/api/v1/chat/sessions/$sessionId/messages',
      queryParameters: {'skip': skip, 'limit': limit},
      cancelToken: cancelToken,
    );
    if (r.data is! Map) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed chat history: not an object');
    }
    try {
      return chatHistoryPageFromJson((r.data as Map).cast<String, dynamic>());
    } on FormatException catch (e) {
      throw ApiException(kind: 'unknown', message: 'malformed chat history', cause: e);
    }
  }

  Future<List<ChatMessage>> getMessages(int sessionId,
      {int skip = 0, int limit = 50, CancelToken? cancelToken}) async {
    final r = await client.dio.get(
      '/api/v1/chat/sessions/$sessionId/messages',
      queryParameters: {'skip': skip, 'limit': limit},
      cancelToken: cancelToken,
    );
    final data = r.data as Map<String, dynamic>;
    return (data['messages'] as List)
        .map((j) => ChatMessage.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> sendMessage(
    int sessionId,
    String content, {
    String lang = 'zh',
    Map<String, dynamic>? quote,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.post(
      '/api/v1/chat/send',
      data: {
        'session_id': sessionId,
        'content': content,
        if (quote != null) 'quote': quote,
      },
      options: Options(headers: {'X-Lang': lang}),
      cancelToken: cancelToken,
    );
    return r.data as Map<String, dynamic>;
  }

  /// SSE 流式发送：逐 `data:` 行解析事件回调 [onEvent]（与手机端同一实现口径）。
  ///
  /// 空闲看门狗：每收到任意一行重置计时，连续 [idleWatchdogLimit] 无字节即判死链
  /// （容忍长上下文构建期首 delta 前的静默，但不允许半开连接永久挂起）。
  Future<void> streamMessage(
    int sessionId,
    String content, {
    String lang = 'zh',
    Map<String, dynamic>? quote,
    bool tts = false,
    bool saveUserMessage = true,
    required void Function(Map<String, dynamic> event) onEvent,
    Duration idleWatchdogLimit = const Duration(seconds: 90),
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.post(
      '/api/v1/chat/sessions/$sessionId/messages/stream',
      data: {
        'content': content,
        if (quote != null) 'quote': quote,
        if (tts) 'tts': true,
        if (!saveUserMessage) 'save_user_message': false,
      },
      options: Options(
        headers: {'X-Lang': lang},
        responseType: ResponseType.stream,
        connectTimeout: const Duration(seconds: 30),
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: Duration.zero,
      ),
      cancelToken: cancelToken,
    );
    final body = r.data as ResponseBody;
    final lines = body.stream
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter());

    final completer = Completer<void>();
    Timer? watchdog;
    void kick() {
      watchdog?.cancel();
      watchdog = Timer(idleWatchdogLimit, () {
        if (!completer.isCompleted) {
          completer.completeError(DioException(
            type: DioExceptionType.receiveTimeout,
            requestOptions: r.requestOptions,
            message: 'SSE idle timeout',
          ));
        }
      });
    }

    final sub = lines.listen(null);
    kick();
    try {
      sub.onData((line) {
        kick();
        final event = parseSseDataLine(line);
        if (event != null) onEvent(event);
      });
      sub.onError((Object e, StackTrace st) {
        watchdog?.cancel();
        if (!completer.isCompleted) completer.completeError(e, st);
      });
      sub.onDone(() {
        watchdog?.cancel();
        if (!completer.isCompleted) completer.complete();
      });
      await completer.future;
    } finally {
      watchdog?.cancel();
      await sub.cancel();
    }
  }
}
