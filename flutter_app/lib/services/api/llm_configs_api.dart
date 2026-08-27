import '../api_client.dart';

/// LlmConfigsApi：用户多 LLM 配置（#68 P0，/api/v1/llm-configs）
extension LlmConfigsApi on ApiClient {
  /// 我的 LLM 配置列表（含主账号共享配置，子账号只读，api_key 不泄）
  Future<List<Map<String, dynamic>>> listLlmConfigs() async {
    final r = await dio.get('/api/v1/llm-configs');
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> createLlmConfig(Map<String, dynamic> body) async {
    final r = await dio.post('/api/v1/llm-configs', data: body);
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateLlmConfig(int id, Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/llm-configs/$id', data: body);
    return r.data as Map<String, dynamic>;
  }

  Future<void> deleteLlmConfig(int id) async {
    await dio.delete('/api/v1/llm-configs/$id');
  }

  Future<Map<String, dynamic>> setLlmConfigDefault(int id) async {
    final r = await dio.post('/api/v1/llm-configs/$id/default');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setLlmConfigShare(int id, bool shared) async {
    final r = await dio.post('/api/v1/llm-configs/$id/share', data: {'shared': shared});
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> testLlmConfig(int id) async {
    final r = await dio.post('/api/v1/llm-configs/$id/test');
    return r.data as Map<String, dynamic>;
  }
}
