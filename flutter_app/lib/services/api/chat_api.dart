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

  /// 发送自定义表情消息：引用已上传表情图片，AI 经表情名描述理解
  Future<Map<String, dynamic>> sendEmojiMessage(
    int sessionId,
    String emojiUrl,
    String name, {
    String lang = 'zh',
  }) async {
    final r = await dio.post(
      '/api/v1/chat/sessions/$sessionId/emoji',
      data: {'emoji_url': emojiUrl, 'name': name},
      options: Options(
        headers: {'X-Lang': lang},
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: const Duration(seconds: 180),
      ),
    );
    return r.data as Map<String, dynamic>;
  }
}
