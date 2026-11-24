import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

/// 头像上传（角色/用户共用）：`POST /api/v1/uploads/avatar` → `{url}`，
/// 再把返回值写回角色 `avatar_url`（`PUT /api/v1/characters/{id}`）。
/// 说明：裁剪由前端按 App 口径做（正方形、512px），本类只负责传输与写回。
class AvatarApi {
  AvatarApi(this.client);

  final AmbApiClient client;

  Future<String> upload(Uint8List bytes, {String filename = 'avatar.png'}) async {
    if (bytes.isEmpty) {
      throw const ApiException(kind: 'client', message: 'empty avatar bytes');
    }
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    final r = await client.dio.post('/api/v1/uploads/avatar', data: form);
    final data = r.data;
    final url = data is Map ? data['url'] : null;
    if (url is! String || url.isEmpty) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed avatar upload: missing url');
    }
    return url;
  }

  Future<void> setAvatar(int characterId, String url) async {
    final r = await client.dio.put('/api/v1/characters/$characterId',
        data: {'avatar_url': url});
    final data = r.data;
    final id = data is Map ? data['id'] : null;
    if (id is! num || id.toInt() != characterId) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed avatar update: id mismatch');
    }
  }
}
