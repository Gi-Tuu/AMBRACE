import 'dart:convert';
import 'dart:io';
import 'package:dio/dio.dart';
import '../../models/message.dart';
import '../api_client.dart';

/// ChatApi：领域 API 方法（extension 挂到 ApiClient）
extension ChatApi on ApiClient {

  Future<Map<String, dynamic>> createSession(int characterId) async {
    final r = await dio.post(
      '/api/v1/chat/sessions',
      queryParameters: {'character_id': characterId},
    );
    return r.data as Map<String, dynamic>;
  }

  Future<List<ChatMessage>> getMessages(int sessionId, {int skip = 0, int limit = 50}) async {
    final r = await dio.get(
      '/api/v1/chat/sessions/$sessionId/messages',
      queryParameters: {'skip': skip, 'limit': limit},
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
  }) async {
    final r = await dio.post(
      '/api/v1/chat/send',
      data: {
        'session_id': sessionId,
        'content': content,
        if (quote != null) 'quote': quote,
      },
      options: Options(headers: {'X-Lang': lang}),
    );
    return r.data as Map<String, dynamic>;
  }

  /// SSE 流式发送：POST /sessions/{id}/messages/stream，逐 `data:` 行解析事件回调 onEvent。
  ///
  /// 事件：{type:user_message|delta|block|done|error|cold_war, ...}。流结束（服务器关闭）或
  /// 出错时返回；调用方用 onEvent 处理增量渲染（delta 打字机 / block·done 确认替换 / error 回退）。
  Future<void> streamMessage(
    int sessionId,
    String content, {
    String lang = 'zh',
    Map<String, dynamic>? quote,
    bool tts = false,
    bool saveUserMessage = true,
    required void Function(Map<String, dynamic> event) onEvent,
  }) async {
    final r = await dio.post(
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
        // SSE 长连接：不设接收超时（默认 10s 会提前断开），连接/发送超时给足
        connectTimeout: const Duration(seconds: 30),
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: Duration.zero,
      ),
    );
    final body = r.data as ResponseBody;
    final lines = body.stream
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter());
    await for (final line in lines) {
      final event = parseSseDataLine(line);
      if (event != null) onEvent(event);
    }
  }

  Future<Map<String, dynamic>> uploadChatImage(
    int sessionId,
    File file, {
    int userId = 1,
    String caption = "",
    String lang = 'zh',
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
      'user_id': userId,
      if (caption.trim().isNotEmpty) 'caption': caption.trim(),
    });
    final r = await dio.post(
      '/api/v1/chat/sessions/$sessionId/image',
      data: form,
      options: Options(
        headers: {'X-Lang': lang},
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: const Duration(seconds: 120),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> uploadChatFile(
    int sessionId,
    File file, {
    String lang = 'zh',
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
    });
    final r = await dio.post(
      '/api/v1/chat/sessions/$sessionId/file',
      data: form,
      options: Options(
        headers: {'X-Lang': lang},
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 180),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> uploadChatVoice(
    int sessionId,
    File file, {
    int durationSec = 0,
    String lang = 'zh',
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
      'duration': '$durationSec',
    });
    final r = await dio.post(
      '/api/v1/chat/sessions/$sessionId/voice',
      data: form,
      options: Options(
        headers: {'X-Lang': lang},
        contentType: 'multipart/form-data',
        connectTimeout: const Duration(seconds: 30),
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 240),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getUnreadCounts() async {
    final r = await dio.get("/api/v1/chat/unread");
    return r.data as Map<String, dynamic>;
  }

  Future<void> markSessionRead(int sessionId) async {
    await dio.post("/api/v1/chat/sessions/$sessionId/read");
  }

  Future<Map<String, dynamic>> getMessage(int messageId) async {
    final r = await dio.get("/api/v1/chat/messages/$messageId");
    return r.data as Map<String, dynamic>;
  }

  Future<void> deleteMessage(int messageId) async {
    await dio.delete("/api/v1/chat/messages/$messageId");
  }

  Future<Map<String, dynamic>> getArchive(int sessionId) async {
    final r = await dio.get(
      '/api/v1/chat/sessions/$sessionId/archive',
    );
    return r.data as Map<String, dynamic>;
  }

  /// 发送表情消息（自定义/市场贴图）：引用图片，AI 经表情名 + 含义描述理解
  Future<Map<String, dynamic>> sendEmojiMessage(
    int sessionId,
    String emojiUrl,
    String name, {
    String lang = 'zh',
    String meaning = '',
  }) async {
    final r = await dio.post(
      '/api/v1/chat/sessions/$sessionId/emoji',
      data: {'emoji_url': emojiUrl, 'name': name, 'meaning': meaning},
      options: Options(
        headers: {'X-Lang': lang},
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: const Duration(seconds: 180),
      ),
    );
    return r.data as Map<String, dynamic>;
  }
}


/// 解析 SSE 单行 `data: {...}`，返回事件 JSON；非事件行/非法 JSON 返回 null。
///
/// 供 chat_provider 事件处理与单元测试复用。
Map<String, dynamic>? parseSseDataLine(String line) {
  final trimmed = line.trim();
  if (trimmed.isEmpty) return null;
  if (!trimmed.startsWith('data: ')) return null;
  final payload = trimmed.substring('data: '.length).trim();
  if (payload.isEmpty) return null;
  try {
    return jsonDecode(payload) as Map<String, dynamic>;
  } catch (_) {
    return null;
  }
}
