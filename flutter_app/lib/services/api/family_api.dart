import '../api_client.dart';

/// FamilyApi：账号关联（#68 P3，/api/v1/account）
extension FamilyApi on ApiClient {
  /// 家庭信息（主账号/子账号两视图）。
  Future<Map<String, dynamic>> getFamily() async {
    final r = await dio.get('/api/v1/account/family');
    return r.data as Map<String, dynamic>;
  }

  /// 独立主账号生成受邀码（复用未过期码；子账号 403）。
  Future<Map<String, dynamic>> generateInvite() async {
    final r = await dio.post('/api/v1/account/invite-code');
    return r.data as Map<String, dynamic>;
  }

  /// 兑换受邀码（5 分钟有效、一次性）。
  Future<Map<String, dynamic>> redeemInvite(String code) async {
    final r = await dio.post('/api/v1/account/link', data: {'code': code});
    return r.data as Map<String, dynamic>;
  }

  /// 解除关联（主账号踢人带 targetUserId；子账号自己解除省略 targetUserId）。
  Future<Map<String, dynamic>> unlink({int? targetUserId}) async {
    final r = await dio.delete(
      '/api/v1/account/link',
      queryParameters: {
        if (targetUserId != null) 'target_user_id': targetUserId,
      },
    );
    return r.data as Map<String, dynamic>;
  }
}
