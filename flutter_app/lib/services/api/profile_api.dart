import 'dart:io';
import 'package:dio/dio.dart';
import '../api_client.dart';

/// ProfileApi：领域 API 方法（extension 挂到 ApiClient）
extension ProfileApi on ApiClient {

  Future<Map<String, dynamic>> uploadAvatar(File file) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
    });
    final r = await dio.post(
      '/api/v1/uploads/avatar',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateProfile(Map<String, dynamic> data) async {
    final r = await dio.put('/api/v1/auth/profile', data: data);
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getRelationships() async {
    final r = await dio.get("/api/v1/relationships");
    return r.data as Map<String, dynamic>;
  }

  Future<void> updateRelationship(int characterId, Map<String, dynamic> data) async {
    await dio.put("/api/v1/relationships/$characterId", data: data);
  }

  Future<void> changePassword({required String oldPassword, required String newPassword}) async {
    await dio.put('/api/v1/auth/password', data: {
      'old_password': oldPassword,
      'new_password': newPassword,
    });
  }
}
