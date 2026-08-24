import 'dart:io';
import 'package:dio/dio.dart';
import '../../models/moment.dart';
import '../../models/moment_comment.dart';
import '../api_client.dart';

/// MomentsApi：领域 API 方法（extension 挂到 ApiClient）
extension MomentsApi on ApiClient {

  Future<List<Moment>> getMoments() async {
    final r = await dio.get("/api/v1/moments");
    final data = r.data as Map<String, dynamic>;
    return (data["moments"] as List)
        .map((j) => Moment.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> publishMoment(int characterId) async {
    final r = await dio.post("/api/v1/moments/publish/$characterId");
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> likeMoment(int momentId) async {
    final r = await dio.post("/api/v1/moments/$momentId/like");
    return r.data as Map<String, dynamic>;
  }

  Future<void> deleteMoment(int momentId) async {
    await dio.delete('/api/v1/moments/$momentId');
  }

  Future<Map<String, dynamic>> publishUserMoment(
    String content, {
    File? image,
  }) async {
    // 后端 create_user_moment 用 Form 接收（图片走 multipart），纯文本也必须走 form，否则 JSON 被忽略返回 400
    final form = FormData.fromMap({
      'content': content,
      if (image != null)
        'image': await MultipartFile.fromFile(image.path, filename: image.uri.pathSegments.last),
    });
    final r = await dio.post(
      '/api/v1/moments/user',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: Duration(seconds: image != null ? 120 : 60),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<List<MomentComment>> getComments(int momentId) async {
    final r = await dio.get("/api/v1/moments/$momentId/comments");
    final data = r.data as Map<String, dynamic>;
    return (data["comments"] as List)
        .map((j) => MomentComment.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> postComment(int momentId, String content, {int? parentId}) async {
    final body = <String, dynamic>{"content": content};
    if (parentId != null) body["parent_id"] = parentId;
    final r = await dio.post("/api/v1/moments/$momentId/comments", data: body);
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getMomentsArchive() async {
    final r = await dio.get("/api/v1/moments/archive");
    return r.data as Map<String, dynamic>;
  }

  Future<void> deleteComment(int momentId, int commentId) async {
    await dio.delete("/api/v1/moments/$momentId/comments/$commentId");
  }

  /// 有 AI 回复我的评论数（朋友圈 tab 红点用）
  Future<int> getUnreadComments() async {
    final r = await dio.get("/api/v1/moments/unread-comments");
    final data = r.data as Map<String, dynamic>;
    return (data["count"] as num?)?.toInt() ?? 0;
  }

  /// 上报已读（进入朋友圈页时调用，重置回复提醒红点）
  Future<void> markMomentsRead() async {
    await dio.post("/api/v1/moments/read");
  }
}
