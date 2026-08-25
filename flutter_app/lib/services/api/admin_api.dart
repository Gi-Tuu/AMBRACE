import '../api_client.dart';

/// AdminApi：主账号管理（#46 选择型）
///
/// - listAccounts：列出全部账号（含 is_admin）供设置页勾选
/// - setAccountAdmin：设置 / 取消某个账号为主账号（后端保证至少保留一个）
extension AdminApi on ApiClient {
  Future<List<Map<String, dynamic>>> listAccounts() async {
    final r = await dio.get('/api/v1/admin/accounts');
    final data = r.data as Map<String, dynamic>;
    return (data['accounts'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> setAccountAdmin(int userId, bool enabled) async {
    final r = await dio.put('/api/v1/admin/accounts/$userId/admin', data: {'enabled': enabled});
    return r.data as Map<String, dynamic>;
  }
}
