import '../api_client.dart';

/// PrivacyApi：AI 隐私上锁（日记 diary / 小手机 phone 查看申请）
extension PrivacyApi on ApiClient {

  /// 锁屏态/冷却/解锁截止；characterId 传 0 时服务端按最近互动角色解析（小手机）
  Future<Map<String, dynamic>> getPrivacyStatus(int characterId, String target) async {
    final r = await dio.get(
      "/api/v1/privacy/$characterId/status",
      queryParameters: {"target": target},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 向 AI 申请查看：返回 {approved, ai_reply, mood_label, unlock_until, cooldown_remaining, ...}
  Future<Map<String, dynamic>> requestPrivacyAccess(int characterId, String target) async {
    final r = await dio.post(
      "/api/v1/privacy/$characterId/request",
      data: {"target": target},
    );
    return r.data as Map<String, dynamic>;
  }
}
