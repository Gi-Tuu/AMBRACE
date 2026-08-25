import '../../models/pet.dart';
import '../api_client.dart';

/// PetsApi：领域 API 方法（extension 挂到 ApiClient）
extension PetsApi on ApiClient {

  Future<List<Pet>> getPets() async {
    final r = await dio.get('/api/v1/pets');
    final data = r.data as Map<String, dynamic>;
    return (data['pets'] as List)
        .map((j) => Pet.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Pet> adoptPet(String species, String name) async {
    final r = await dio.post('/api/v1/pets', data: {'species': species, 'name': name});
    return Pet.fromJson(r.data as Map<String, dynamic>);
  }

  Future<Pet> petAction(int id, String action) async {
    final r = await dio.post('/api/v1/pets/$id/$action');
    return Pet.fromJson(r.data as Map<String, dynamic>);
  }

  Future<Pet> renamePet(int id, String name) async {
    final r = await dio.post('/api/v1/pets/$id/rename', data: {'name': name});
    return Pet.fromJson(r.data as Map<String, dynamic>);
  }

  Future<void> deletePet(int id) async {
    await dio.delete('/api/v1/pets/$id');
  }

  /// actor=ai 时只返回角色自己照顾的记录（小手机宠物应用用）
  Future<List<Map<String, dynamic>>> getPetActivities(
    int id, {
    int limit = 10,
    String? actor,
  }) async {
    final r = await dio.get('/api/v1/pets/$id/activities', queryParameters: {
      'limit': limit,
      if (actor != null) 'actor': actor,
    });
    return parseListItems(r.data, 'activities', (j) => j as Map<String, dynamic>);
  }

  /// 用户所有角色的 AI 宠物（拜访/代为领养面板）：[{character_id, character_name, pet|null}]
  Future<List<Map<String, dynamic>>> getAiPets() async {
    final r = await dio.get('/api/v1/pets/ai-pets');
    return parseListItems(r.data, 'characters', (j) => j as Map<String, dynamic>);
  }

  /// 用户代为领养：为指定角色领养 AI 宠物（每角色 ≤1 只）
  Future<Pet> aiAdopt(int characterId, String species, String name) async {
    final r = await dio.post('/api/v1/pets/ai-adopt',
        data: {'character_id': characterId, 'species': species, 'name': name});
    return Pet.fromJson(r.data as Map<String, dynamic>);
  }
}
