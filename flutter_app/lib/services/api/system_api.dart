import '../api_client.dart';

/// SystemApi：系统级配置（LLM / 生图，用户级 BYOK + 服务器级全局）
extension SystemApi on ApiClient {
  // ── 用户级 BYOK（我的 LLM）──
  Future<Map<String, dynamic>> getApiConfig() async {
    final r = await dio.get('/api/v1/system/api-config');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateApiConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/api-config', data: body);
    return r.data as Map<String, dynamic>;
  }

  // ── 服务器级 LLM（仅主账号）──
  Future<Map<String, dynamic>> getServerApiConfig() async {
    final r = await dio.get('/api/v1/system/api-config/server');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateServerApiConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/api-config/server', data: body);
    return r.data as Map<String, dynamic>;
  }

  // ── 服务器级生图（仅主账号）──
  Future<Map<String, dynamic>> getImageGenServerConfig() async {
    final r = await dio.get('/api/v1/system/image-gen-config/server');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateImageGenServerConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/image-gen-config/server', data: body);
    return r.data as Map<String, dynamic>;
  }
  // ── 服务器级识图（图片理解，仅主账号）──
  Future<Map<String, dynamic>> getVlmServerConfig() async {
    final r = await dio.get('/api/v1/system/vlm-config/server');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateVlmServerConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/vlm-config/server', data: body);
    return r.data as Map<String, dynamic>;
  }

  // ── 服务器级语音大模型（仅主账号；当前转写走本地 whisper，配置先占位）──
  Future<Map<String, dynamic>> getSpeechServerConfig() async {
    final r = await dio.get('/api/v1/system/speech-config/server');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateSpeechServerConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/speech-config/server', data: body);
    return r.data as Map<String, dynamic>;
  }

  /// 音色试听：固定文案合成当前音色/语速/语调，返回音频相对 URL；失败返回空串
  Future<String> speechPreview({
    String voice = '',
    double voiceRate = 1.0,
    double voicePitch = 0.0,
    String gender = '',
  }) async {
    try {
      final r = await dio.post('/api/v1/system/speech-preview', data: {
        'voice': voice,
        'voice_rate': voiceRate,
        'voice_pitch': voicePitch,
        'gender': gender,
      });
      return (r.data as Map<String, dynamic>)['url'] as String? ?? '';
    } catch (_) {
      return '';
    }
  }

  // ── 服务器级全模态大模型（仅主账号）──
  Future<Map<String, dynamic>> getMultimodalServerConfig() async {
    final r = await dio.get('/api/v1/system/multimodal-config/server');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateMultimodalServerConfig(Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/multimodal-config/server', data: body);
    return r.data as Map<String, dynamic>;
  }
  // ── 任务专用模型（按用途指定；P1②，2026-08-12）──
  Future<List<Map<String, dynamic>>> getTaskLlmCatalog() async {
    final r = await dio.get('/api/v1/system/api-config/tasks');
    final tasks = (r.data as Map<String, dynamic>)['tasks'] as List<dynamic>? ?? [];
    return tasks.map((e) => Map<String, dynamic>.from(e as Map)).toList();
  }

  Future<Map<String, dynamic>> getServerTaskApiConfig(String task) async {
    final r = await dio.get('/api/v1/system/api-config/task/server/$task');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateServerTaskApiConfig(String task, Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/system/api-config/task/server/$task', data: body);
    return r.data as Map<String, dynamic>;
  }

  /// 连接测试：最小请求校验配置；成功返回 ok/耗时/Key 尾号，失败返回 error
  Future<Map<String, dynamic>> testApiConnection(Map<String, dynamic> body) async {
    final r = await dio.post('/api/v1/system/api-config/test', data: body);
    return r.data as Map<String, dynamic>;
  }

  // ── LLM token 用量与免费额度（2026-08-11）──
  Future<Map<String, dynamic>> getLlmUsage() async {
    final r = await dio.get('/api/v1/system/llm-usage');
    return r.data as Map<String, dynamic>;
  }

  Future<void> updateLlmUsageLimit(int totalLimit) async {
    await dio.put('/api/v1/system/llm-usage/limit', data: {'total_limit': totalLimit});
  }
}
