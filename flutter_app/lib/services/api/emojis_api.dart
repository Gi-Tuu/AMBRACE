import 'package:dio/dio.dart';
import '../api_client.dart';

/// EmojisApi：表情包领域 API（extension 挂到 ApiClient）
extension EmojisApi on ApiClient {
  Future<List<Map<String, dynamic>>> getEmojiPacks() async {
    final r = await dio.get('/api/v1/emojis/packs');
    return parseListItems(r.data, 'packs', (j) => j as Map<String, dynamic>);
  }

  Future<bool> downloadEmojiPack(String packId) async {
    final r = await dio.post('/api/v1/emojis/packs/$packId/download');
    return r.statusCode == 200;
  }

  Future<bool> removeEmojiPack(String packId) async {
    final r = await dio.delete('/api/v1/emojis/packs/$packId');
    return r.statusCode == 200;
  }

  Future<List<Map<String, dynamic>>> getCustomEmojis() async {
    final r = await dio.get('/api/v1/emojis/custom');
    return parseListItems(r.data, 'emojis', (j) => j as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> uploadCustomEmoji(dynamic file, String name) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
      'name': name,
    });
    final r = await dio.post(
      '/api/v1/emojis/custom',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<bool> deleteCustomEmoji(int emojiId) async {
    final r = await dio.delete('/api/v1/emojis/custom/$emojiId');
    return r.statusCode == 200;
  }
}
