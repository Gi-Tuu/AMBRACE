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

  /// P2-11（2026-09-28）：回拉单条记忆的**完整**记录。
  ///
  /// 织库等入口传给详情页的 Memory 是精简对象（只映射了 id/类型/内容/重要度等，
  /// 没有 why_it_matters），导致详情页「意义」卡片永远不显示。
  /// GET /api/v1/memories/{id} 返回完整 MemoryResponse，前端建好后回拉一次补齐。
  Future<Memory> getMemory(int id) async {
    final r = await dio.get("/api/v1/memories/$id");
    return Memory.fromJson(Map<String, dynamic>.from(r.data as Map));
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

  /// 批 0-2 / M1b「这是真的」：认可一条手机观察记忆（后端只升认知状态，来源保持不变）。
  Future<void> acceptPerceptionMemory(int id) async {
    await updateMemory(id, {'epistemic_status': 'FACT'});
  }

  /// 批 0-2 / M1b「不记住」：撤回＝归档（复用既有 is_archived，不删行、可逆）。
  Future<void> archiveMemory(int id) async {
    await updateMemory(id, {'is_archived': true});
  }

  /// 记忆链条全时间线（同链 root→branch… 时间升序，含自身；未建链时仅自身）。
  ///
  /// 旧实现走 `DELETE /{id}/tree?cascade=false` 借删除接口读子节点（只看子、且动词不当），
  /// 叶子/单节点记忆在链条卡里一律显示为空——改走只读的 GET /{id}/chain。
  Future<List<MemoryNode>> getMemoryChain(int id) async {
    final r = await dio.get('/api/v1/memories/$id/chain');
    final data = r.data as Map<String, dynamic>;
    return (data['chain'] as List)
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
