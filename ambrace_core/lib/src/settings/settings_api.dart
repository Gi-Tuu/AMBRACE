import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

class DndSettings {
  const DndSettings({
    this.enabled = false,
    this.notificationsEnabled = true,
    this.startHour = 22,
    this.startMinute = 0,
    this.endHour = 8,
    this.endMinute = 0,
  });

  final bool enabled;
  final bool notificationsEnabled;
  final int startHour;
  final int startMinute;
  final int endHour;
  final int endMinute;

  DndSettings copyWith({
    bool? enabled,
    bool? notificationsEnabled,
    int? startHour,
    int? startMinute,
    int? endHour,
    int? endMinute,
  }) =>
      DndSettings(
        enabled: enabled ?? this.enabled,
        notificationsEnabled: notificationsEnabled ?? this.notificationsEnabled,
        startHour: startHour ?? this.startHour,
        startMinute: startMinute ?? this.startMinute,
        endHour: endHour ?? this.endHour,
        endMinute: endMinute ?? this.endMinute,
      );

  /// 后端 `PUT /auth/dnd` 的字段形状（与 Flutter DndSettings.syncToServer 一致）。
  Map<String, dynamic> toServerJson() => {
        'dnd_enabled': enabled,
        'notifications_enabled': notificationsEnabled,
        'start_hour': startHour,
        'start_minute': startMinute,
        'end_hour': endHour,
        'end_minute': endMinute,
      };
}

class SettingsApi {
  SettingsApi(this.client);

  final AmbApiClient client;

  Future<void> updateDnd(DndSettings settings, {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/auth/dnd',
        data: settings.toServerJson(), cancelToken: cancelToken);
    if (r.statusCode != 200) {
      throw ApiException(
          kind: 'unknown', message: 'dnd update failed: ${r.statusCode}');
    }
  }

  Future<Map<String, dynamic>> profile({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/auth/profile', cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed profile');
    }
    return data.cast<String, dynamic>();
  }
}
