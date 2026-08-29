import '../api_client.dart';

/// LifeHomeApi：生活可视·小家领域 API（v3.0.0）
extension LifeHomeApi on ApiClient {
  /// 读取小家状态：角色名/体力/心情/饥饿/宠物列表；characterId=0 走最近互动角色
  Future<Map<String, dynamic>> getLifeHomeState({int characterId = 0}) async {
    final r = await dio.get(
      '/api/v1/life-home/state',
      queryParameters: {'character_id': characterId},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 小家交互事件：生活动作结算状态 + 日志；petId 用于宠物互动（pet_feed/pet_pet）
  Future<Map<String, dynamic>> postLifeHomeEvent({
    required int characterId,
    required String action,
    int? petId,
  }) async {
    final r = await dio.post(
      '/api/v1/life-home/event',
      data: {
        'character_id': characterId,
        'action': action,
        if (petId != null) 'pet_id': petId,
      },
    );
    return r.data as Map<String, dynamic>;
  }
}