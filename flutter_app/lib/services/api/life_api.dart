import '../api_client.dart';

/// LifeApi：AI 伙伴生活（Life Engine v2，2026-08-12）
extension LifeApi on ApiClient {
  /// AI 生活时间线（source=life 记忆，时间倒序）
  Future<List<Map<String, dynamic>>> getLifeTimeline({int? characterId, int limit = 50}) async {
    final r = await dio.get('/api/v1/life/timeline', queryParameters: {
      'limit': limit,
      if (characterId != null) 'character_id': characterId,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// Life State（energy/focus/needs/phase）
  Future<Map<String, dynamic>> getLifeState(int characterId) async {
    final r = await dio.get('/api/v1/life/state', queryParameters: {'character_id': characterId});
    return r.data as Map<String, dynamic>;
  }

  /// AI 生活产物库（Phase 2：创作/浏览/学习产物，时间倒序）
  Future<List<Map<String, dynamic>>> getLifeArtifacts({int? characterId, String? type, int limit = 50}) async {
    final r = await dio.get('/api/v1/life/artifacts', queryParameters: {
      'limit': limit,
      if (characterId != null) 'character_id': characterId,
      if (type != null) 'type': type,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// AI 生活兴趣（Phase 3：按等级降序）
  Future<List<Map<String, dynamic>>> getLifeInterests(int characterId) async {
    final r = await dio.get('/api/v1/life/interests', queryParameters: {'character_id': characterId});
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// AI 生活目标（Phase 3：active 优先，按优先级降序）
  Future<List<Map<String, dynamic>>> getLifeGoals(int characterId) async {
    final r = await dio.get('/api/v1/life/goals', queryParameters: {'character_id': characterId});
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// AI 真实浏览记录（Phase B，2026-08-14：browse/learn 活动的真实网页记录）
  Future<List<Map<String, dynamic>>> getLifeBrowsing({int? characterId, int limit = 50}) async {
    final r = await dio.get('/api/v1/life/browsing', queryParameters: {
      'limit': limit,
      if (characterId != null) 'character_id': characterId,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// AI 日程（Phase B-2，2026-08-14：固定作息/Goal 推导/AI 自生成，按开始时间倒序）
  Future<List<Map<String, dynamic>>> getLifeSchedules(int characterId, {String? date, int limit = 30}) async {
    final r = await dio.get('/api/v1/life/schedules', queryParameters: {
      'character_id': characterId,
      'limit': limit,
      if (date != null) 'date': date,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }
}
