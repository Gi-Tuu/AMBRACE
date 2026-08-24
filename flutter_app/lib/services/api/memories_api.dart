import '../../models/memory.dart';
import '../api_client.dart';

/// MemoriesApi：领域 API 方法（extension 挂到 ApiClient）
extension MemoriesApi on ApiClient {

  Future<List<Memory>> getMemories({int? characterId}) async {
    final res = await getMemoriesWithTotal(characterId: characterId);
    return res.memories;
  }

  Future<({List<Memory> memories, int total})> getMemoriesWithTotal({int? characterId}) async {
    final params = <String, dynamic>{};
    if (characterId != null) params['character_id'] = characterId;
    final r = await dio.get('/api/v1/memories', queryParameters: params);
    final data = r.data as Map<String, dynamic>;
    final memories = (data['memories'] as List)
        .map((j) => Memory.fromJson(j as Map<String, dynamic>))
        .toList();
    return (memories: memories, total: data['total'] as int? ?? memories.length);
  }

  Future<void> updateMemory(int id, Map<String, dynamic> data) async {
    await dio.patch("/api/v1/memories/$id", data: data);
  }

  Future<void> deleteMemory(int id) async {
    await dio.delete('/api/v1/memories/$id');
  }

  Future<void> updateMemoryContent(int id, String content) async {
    await dio.patch('/api/v1/memories/$id/content', data: {'content': content});
  }

  Future<List<MemoryNode>> getMemoryChildren(int id) async {
    final r = await dio.delete('/api/v1/memories/$id/tree', queryParameters: {'cascade': 'false'});
    final data = r.data as Map<String, dynamic>;
    return (data['children'] as List)
        .map((j) => MemoryNode.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<void> deleteMemoryCascade(int id) async {
    await dio.delete('/api/v1/memories/$id/tree', queryParameters: {'cascade': 'true'});
  }

  Future<Map<String, dynamic>> summarizeMemories(int characterId, String memoryType, {bool force = false}) async {
    final r = await dio.post(
      '/api/v1/memories/$characterId/summarize',
      queryParameters: {'memory_type': memoryType, if (force) 'force': 'true'},
    );
    return r.data as Map<String, dynamic>;
  }
}
