import '../api_client.dart';

/// PermissionApi：AI 能力权限（extension 挂到 ApiClient，2026-08-12）
extension PermissionApi on ApiClient {
  /// 权限配置：{global_level, scopes: {scope: level}}
  Future<Map<String, dynamic>> getPermissions() async {
    final r = await dio.get('/api/v1/permissions');
    return r.data as Map<String, dynamic>;
  }

  /// 批量更新权限档位（仅传需要修改的字段）
  Future<Map<String, dynamic>> updatePermissions({
    String? globalLevel,
    Map<String, String>? scopes,
  }) async {
    final r = await dio.put(
      '/api/v1/permissions',
      data: {
        if (globalLevel != null) 'global_level': globalLevel,
        if (scopes != null) 'scopes': scopes,
      },
    );
    return r.data as Map<String, dynamic>;
  }

  /// 批准待确认动作（如生图）→ 后端立即执行
  Future<void> approvePermissionAction(int actionId) async {
    await dio.post('/api/v1/permissions/actions/$actionId/approve');
  }

  /// 拒绝待确认动作
  Future<void> denyPermissionAction(int actionId) async {
    await dio.post('/api/v1/permissions/actions/$actionId/deny');
  }
}
