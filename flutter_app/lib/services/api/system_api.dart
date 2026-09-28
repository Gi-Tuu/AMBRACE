import 'dart:typed_data';

import 'package:dio/dio.dart';

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
  // ── 运行时 Feature Flag 开关（仅主账号；2026-08-18）──
  Future<List<Map<String, dynamic>>> getFeatureFlags() async {
    final r = await dio.get('/api/v1/system/feature-flags');
    final data = r.data as Map<String, dynamic>;
    return (data['flags'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> updateFeatureFlag(String key, bool enabled) async {
    final r = await dio.put('/api/v1/system/feature-flags/$key', data: {'enabled': enabled});
    return r.data as Map<String, dynamic>;
  }

  /// 上下文预算读数（P2b，2026-09-24）：P2a 预留口径 + 本账号最近一次系统块超预算被裁的埋点。
  /// 纯读，任何登录用户只读自己的数据。
  Future<Map<String, dynamic>> getContextBudget() async {
    final r = await dio.get('/api/v1/system/context-budget');
    return r.data as Map<String, dynamic>;
  }

  /// 档位切换（S2）：body 只带 tier；服务端对脏输入归一/夹紧后回落标准档，
  /// 返回 {status, tier, tier_budget_tokens, previous_tier}。
  Future<Map<String, dynamic>> setContextBudgetTier(String tier) async {
    final r = await dio.put('/api/v1/system/context-budget/tier', data: {'tier': tier});
    return r.data as Map<String, dynamic>;
  }

  Future<ContextBudgetInfo> getContextBudgetInfo() async =>
      ContextBudgetInfo.fromMap(await getContextBudget());

  // ── 备份一键导出（#54，2026-08-23：仅主账号）──

  /// 触发备份：返回 {path, size, created_at}（当天已存在则直接返回现有文件）
  Future<Map<String, dynamic>> triggerBackup() async {
    final r = await dio.post('/api/v1/system/backup');
    return r.data as Map<String, dynamic>;
  }

  /// 下载备份 zip（字节），供保存到手机
  Future<Uint8List> downloadBackupBytes() async {
    final r = await dio.get<List<int>>(
      '/api/v1/system/backup/download',
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(r.data ?? <int>[]);
  }

  /// 备份下载直链（供无法原生保存时用浏览器/电脑打开）
  String get backupDownloadUrl =>
      '${baseUrl.replaceAll(RegExp(r'/+$'), '')}/api/v1/system/backup/download';
}

/// 上下文预算档位选项（数值全部来自服务端，前端不复制算式、不写死 token 数）。
class ContextBudgetTierOption {
  const ContextBudgetTierOption({
    required this.key,
    required this.budgetTokens,
    required this.isCurrent,
  });

  final String key;
  final int budgetTokens;
  final bool isCurrent;

  factory ContextBudgetTierOption.fromMap(Map<dynamic, dynamic> m) =>
      ContextBudgetTierOption(
        key: (m['key'] ?? '').toString(),
        budgetTokens: (m['budget_tokens'] as num?)?.toInt() ?? 0,
        isCurrent: m['is_current'] == true,
      );
}

/// 最近一轮实际占用；status != ok 表示无样本（不许拿预算数冒充占用）。
class ContextBudgetUsage {
  const ContextBudgetUsage({
    required this.status,
    this.estTokens,
    this.createdAt,
  });

  final String status;
  final int? estTokens;
  final String? createdAt;

  bool get hasSample => status == 'ok' && estTokens != null;

  factory ContextBudgetUsage.fromMap(Object? raw) {
    if (raw is! Map) return const ContextBudgetUsage(status: 'unknown');
    final m = Map<dynamic, dynamic>.from(raw);
    return ContextBudgetUsage(
      status: (m['status'] ?? 'unknown').toString(),
      estTokens: (m['est_tokens'] as num?)?.toInt(),
      createdAt: m['created_at']?.toString(),
    );
  }
}

/// 最近一次系统块超预算被裁的留痕（无则 null）。
class ContextBudgetClip {
  const ContextBudgetClip({this.characterId, this.createdAt});

  final String? characterId;
  final String? createdAt;

  factory ContextBudgetClip.fromMap(Map<dynamic, dynamic> m) => ContextBudgetClip(
        characterId: m['character_id']?.toString(),
        createdAt: m['created_at']?.toString(),
      );
}

/// GET /api/v1/system/context-budget 的解析结果。
class ContextBudgetInfo {
  const ContextBudgetInfo({
    required this.tier,
    required this.tierSource,
    required this.tierOptions,
    required this.tierCeilingTokens,
    required this.effectiveBudgetTokens,
    required this.reserveReplyTokens,
    required this.reserveToolsTokens,
    required this.floorTokens,
    required this.totalQuotaTokens,
    required this.lastUsage,
    required this.clipCount24h,
    this.lastClip,
    this.error = '',
  });

  final String tier;

  /// user=账号显式设置 / default=未设置（等价标准档）/ unavailable=读库失败
  final String tierSource;
  final List<ContextBudgetTierOption> tierOptions;
  final int tierCeilingTokens;
  final int effectiveBudgetTokens;
  final int reserveReplyTokens;
  final int reserveToolsTokens;
  final int floorTokens;
  final int totalQuotaTokens;
  final ContextBudgetUsage lastUsage;
  final int clipCount24h;
  final ContextBudgetClip? lastClip;
  final String error;

  bool get isUserChosen => tierSource == 'user';

  factory ContextBudgetInfo.fromMap(Map<String, dynamic> m) {
    final options = (m['tier_options'] as List<dynamic>? ?? [])
        .whereType<Map>()
        .map(ContextBudgetTierOption.fromMap)
        .toList();
    final clip = m['last_clip'];
    return ContextBudgetInfo(
      tier: (m['tier'] ?? 'standard').toString(),
      tierSource: (m['tier_source'] ?? 'default').toString(),
      tierOptions: options,
      tierCeilingTokens: (m['tier_ceiling_tokens'] as num?)?.toInt() ?? 0,
      effectiveBudgetTokens: (m['effective_budget_tokens'] as num?)?.toInt() ?? 0,
      reserveReplyTokens: (m['reserve_reply_tokens'] as num?)?.toInt() ?? 0,
      reserveToolsTokens: (m['reserve_tools_tokens'] as num?)?.toInt() ?? 0,
      floorTokens: (m['floor_tokens'] as num?)?.toInt() ?? 0,
      totalQuotaTokens: (m['total_quota_tokens'] as num?)?.toInt() ?? 0,
      lastUsage: ContextBudgetUsage.fromMap(m['last_usage']),
      clipCount24h: (m['clip_count_24h'] as num?)?.toInt() ?? 0,
      lastClip: clip is Map ? ContextBudgetClip.fromMap(clip) : null,
      error: (m['error'] ?? '').toString(),
    );
  }
}
