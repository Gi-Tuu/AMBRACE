import '../../models/character.dart';
import '../../models/character_state.dart';
import '../../models/emotion_event.dart';
import '../api_client.dart';

/// CharactersApi：领域 API 方法（extension 挂到 ApiClient）
extension CharactersApi on ApiClient {

  Future<List<AICharacter>> getCharacters() async {
    final r = await dio.get('/api/v1/characters');
    final data = r.data as Map<String, dynamic>;
    return (data['characters'] as List)
        .map((j) => AICharacter.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<AICharacter> createCharacter(Map<String, dynamic> data) async {
    final r = await dio.post('/api/v1/characters', data: data);
    return AICharacter.fromJson(r.data as Map<String, dynamic>);
  }

  /// 读取单个角色详情（含认知循环开关等角色级字段，2026-08-24）
  Future<AICharacter> getCharacter(int id) async {
    final r = await dio.get('/api/v1/characters/$id');
    return AICharacter.fromJson(r.data as Map<String, dynamic>);
  }

  Future<AICharacter> updateCharacter(int id, Map<String, dynamic> data) async {
    final r = await dio.put('/api/v1/characters/$id', data: data);
    return AICharacter.fromJson(r.data as Map<String, dynamic>);
  }

  Future<void> deleteCharacter(int id) async {
    await dio.delete('/api/v1/characters/$id');
  }

  /// 为角色生成一句符合人设的开场白（LLM），写回 greeting_message（创建后一次性触发）。
  Future<Map<String, dynamic>> generateGreeting(int id) async {
    final r = await dio.post('/api/v1/characters/$id/generate-greeting');
    return r.data as Map<String, dynamic>;
  }

  Future<CharacterState> getCharacterStates(int characterId) async {
    final r = await dio.get('/api/v1/characters/$characterId/states');
    return CharacterState.fromJson(r.data as Map<String, dynamic>);
  }

  Future<EmotionTimeline> getEmotionTimeline(int characterId, {int days = 7}) async {
    final r = await dio.get('/api/v1/characters/$characterId/emotion-timeline',
        queryParameters: {'days': days});
    return EmotionTimeline.fromJson(r.data as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> getSchedulerSettings(int characterId) async {
    final r = await dio.get("/api/v1/scheduler/settings/$characterId");
    return r.data as Map<String, dynamic>;
  }

  Future<void> updateSchedulerSettings(int characterId, Map<String, dynamic> data) async {
    await dio.put("/api/v1/scheduler/settings/$characterId", data: data);
  }

  Future<Map<String, dynamic>> getStateHistory(int characterId, {int days = 30}) async {
    final r = await dio.get(
      '/api/v1/characters/$characterId/state-history',
      queryParameters: {'days': days},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 事件时钟：列出未到期的定时承诺（私聊右上角可视化展示，2026-08-15）
  Future<List<Map<String, dynamic>>> listTimers(int characterId) async {
    final r = await dio.get('/api/v1/scheduler/timers/$characterId');
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }

  /// 事件时钟：删除一条定时承诺（用户主动取消）
  Future<void> deleteTimer(int characterId, int eventId) async {
    await dio.delete('/api/v1/scheduler/timers/$characterId/$eventId');
  }
}

