import '../api_client.dart';

/// PlatformProfileApi：平台档案（抖音公开记忆收紧开关）
extension PlatformProfileApi on ApiClient {
  /// 读取抖音平台档案（memory_restrict: off / relationship）
  Future<Map<String, dynamic>> getDouyinProfile() async {
    final r = await dio.get('/api/v1/platform-profile/douyin');
    return r.data as Map<String, dynamic>;
  }

  /// 更新抖音平台档案（仅主账号）
  Future<Map<String, dynamic>> updateDouyinProfile(String memoryRestrict) async {
    final r = await dio.put('/api/v1/platform-profile/douyin', data: {'memory_restrict': memoryRestrict});
    return r.data as Map<String, dynamic>;
  }
}
