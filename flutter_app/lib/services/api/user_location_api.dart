import '../api_client.dart';

/// UserLocationApi：位置信息（总开关 / 获取地理位置 / 用户位置 / AI 位置 / 位置跟随 / 时区 / GPS 坐标）
extension UserLocationApi on ApiClient {
  Future<Map<String, dynamic>> getUserLocation() async {
    final r = await dio.get("/api/v1/users/location");
    return r.data as Map<String, dynamic>;
  }

  /// 提交位置设置；timezoneOffsetMinutes 为手机本地时区（分钟，如 480=UTC+8），null 表示不更新
  Future<Map<String, dynamic>> updateUserLocation({
    bool? locationEnabled,
    bool? locationGpsEnabled,
    String? userLocation,
    String? aiLocation,
    bool? locationFollow,
    int? timezoneOffsetMinutes,
    double? locationLat,
    double? locationLng,
  }) async {
    final payload = <String, dynamic>{};
    if (locationEnabled != null) payload["location_enabled"] = locationEnabled;
    if (locationGpsEnabled != null) payload["location_gps_enabled"] = locationGpsEnabled;
    if (userLocation != null) payload["user_location"] = userLocation;
    if (aiLocation != null) payload["ai_location"] = aiLocation;
    if (locationFollow != null) payload["location_follow"] = locationFollow;
    if (timezoneOffsetMinutes != null) {
      payload["timezone_offset_minutes"] = timezoneOffsetMinutes;
    }
    if (locationLat != null) payload["location_lat"] = locationLat;
    if (locationLng != null) payload["location_lng"] = locationLng;
    final r = await dio.put("/api/v1/users/location", data: payload);
    return r.data as Map<String, dynamic>;
  }
}
