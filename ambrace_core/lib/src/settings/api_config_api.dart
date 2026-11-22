/// 用户多 LLM 配置与用量（设置页「模型与 API」）客户端。
///
/// 契约以 `backend/app/api/llm_configs.py` 与 `backend/app/api/system.py` 为准：
/// - GET    `/api/v1/llm-configs` → `{items:[...]}`；子账号额外含主账号共享配置
///   （`is_shared=true`，其 `api_key` 恒为空串、只读）；
/// - POST   `/api/v1/llm-configs`（body：name/base_url/model/provider/enabled/
///   is_default/shared_with_subs[，api_key 仅非空才带]）→ 配置 Map；
/// - PUT    `/api/v1/llm-configs/{id}` → 配置 Map（仅本人；共享配置禁止改）；
/// - DELETE `/api/v1/llm-configs/{id}` → 204（引用角色自动置 NULL）；
/// - POST   `/api/v1/llm-configs/{id}/default` → Map（同用户默认唯一）；
/// - POST   `/api/v1/llm-configs/{id}/share` `{shared}` → Map；
/// - POST   `/api/v1/llm-configs/{id}/test` → `{ok,latency_ms,model,api_key_tail}`；
/// - GET    `/api/v1/system/llm-usage` → 用量统计（today/week/month/used_total/
///   remaining/total_limit/limit_source/by_model）。
///
/// 纪律与 [EditorApi] 一致：全部经同一个 [AmbApiClient]（身份验签 / 401 钩子 /
/// 错误归一），不另造 Dio、不绕过认证。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

class ApiConfigApi {
  ApiConfigApi(this.client);

  final AmbApiClient client;

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    throw ApiException(
        kind: 'unknown', message: 'malformed $what', cause: data.runtimeType);
  }

  /// 我的配置列表（含共享只读项；`api_key` 只回掩码/空串，明文不出网）。
  Future<List<Map<String, dynamic>>> list({CancelToken? cancelToken}) async {
    final r =
        await client.dio.get('/api/v1/llm-configs', cancelToken: cancelToken);
    final j = _obj(r.data, 'llm-configs');
    final items = j['items'];
    if (items is! List) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed llm-configs items');
    }
    return items.cast<Map>().map((e) => e.cast<String, dynamic>()).toList();
  }

  Future<Map<String, dynamic>> create(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/llm-configs',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'llm-config create');
  }

  Future<Map<String, dynamic>> update(int id, Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/llm-configs/$id',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'llm-config update');
  }

  Future<void> delete(int id, {CancelToken? cancelToken}) async {
    await client.dio.delete('/api/v1/llm-configs/$id', cancelToken: cancelToken);
  }

  Future<Map<String, dynamic>> setDefault(int id,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/llm-configs/$id/default',
        cancelToken: cancelToken);
    return _obj(r.data, 'llm-config default');
  }

  Future<Map<String, dynamic>> setShare(int id, bool shared,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/llm-configs/$id/share',
        data: {'shared': shared}, cancelToken: cancelToken);
    return _obj(r.data, 'llm-config share');
  }

  /// 连接测试：成功 `{ok:true,latency_ms,model,api_key_tail}`；
  /// 失败也可能是 HTTP 200 下的 `ok:false` Map，由调用方按 `ok` 分支展示。
  Future<Map<String, dynamic>> test(int id, {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/llm-configs/$id/test',
        cancelToken: cancelToken);
    return _obj(r.data, 'llm-config test');
  }

  /// Token 用量统计（今日/近7天/本月/累计/剩余额度/按模型）。
  Future<Map<String, dynamic>> usage({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/llm-usage',
        cancelToken: cancelToken);
    return _obj(r.data, 'llm-usage');
  }

  // ── 任务级 LLM 配置（用户级；8 任务 = memory/card/emotion/status/review/
  //    message/diary/timeline；speech/vlm/image_gen 同形但不进目录）──────────

  /// 任务目录：`GET /api/v1/system/api-config/tasks` → `{tasks:[{task,name,desc}]}`。
  Future<List<Map<String, dynamic>>> taskCatalog(
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/api-config/tasks',
        cancelToken: cancelToken);
    final j = _obj(r.data, 'api-config tasks');
    final items = j['tasks'];
    if (items is! List) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed api-config tasks');
    }
    return items.cast<Map>().map((e) => e.cast<String, dynamic>()).toList();
  }

  /// 单任务配置：`GET /api/v1/system/api-config/task/{task}` →
  /// `{enabled,base_url,model,provider,has_api_key,configured}`。
  Future<Map<String, dynamic>> getTaskConfig(String task,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/api-config/task/$task',
        cancelToken: cancelToken);
    return _obj(r.data, 'task api-config');
  }

  /// 写单任务配置（upsert 部分更新；body 键缺省＝保留现值，
  /// `api_key` 只在非空时带上——空串语义是**清除**）。→ `{status,task,enabled,configured}`。
  Future<Map<String, dynamic>> putTaskConfig(String task,
      Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/api-config/task/$task',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'task api-config update');
  }

  /// 连接测试（payload 型，不需要已保存配置）：`POST /api/v1/system/api-config/test`，
  /// `{base_url,api_key,model[,provider]}`；api_key 留空＝后端回落服务器级全局配置。
  Future<Map<String, dynamic>> testConnection(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/system/api-config/test',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'api-config test');
  }

  // ── 设置页三期：更新公告 / Feature Flags / 上下文预算 ─────────────────────

  /// 更新公告：`GET /api/v1/system/updates` →
  /// `{days:[{date,title,items:[{content,reason}]}]}`（最新在前）。
  Future<Map<String, dynamic>> updates({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/updates',
        cancelToken: cancelToken);
    return _obj(r.data, 'updates');
  }

  /// Feature Flags 全量（含 meta 展示元数据 + scope/user_enabled）：
  /// `GET /api/v1/system/feature-flags` → `{status,flags:[...]}`。
  Future<Map<String, dynamic>> featureFlags({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/feature-flags',
        cancelToken: cancelToken);
    return _obj(r.data, 'feature-flags');
  }

  /// 切换 Flag：`PUT /api/v1/system/feature-flags/{key}` `{enabled}`。
  /// 用户语义键写本账号覆盖行，其余写全局；锁定/非自助键 403（由调用方展示）。
  Future<Map<String, dynamic>> setFeatureFlag(String key, bool enabled,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/feature-flags/$key',
        data: {'enabled': enabled}, cancelToken: cancelToken);
    return _obj(r.data, 'feature-flag update');
  }

  /// 上下文预算快照（S2 档位 + 预留口径 + 实际占用/裁剪埋点 + 每层体量 + 费用估算）。
  Future<Map<String, dynamic>> contextBudget({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/context-budget',
        cancelToken: cancelToken);
    return _obj(r.data, 'context-budget');
  }

  /// 设置本账号上下文预算档位（standard / extended / max；脏值服务端夹取）。
  Future<Map<String, dynamic>> setContextBudgetTier(String tier,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/context-budget/tier',
        data: {'tier': tier}, cancelToken: cancelToken);
    return _obj(r.data, 'context-budget tier');
  }

  // ── 服务器级配置（仅主账号；PWA 管理面 2026-11-22 批 12）────────────────

  /// 服务器级 LLM 全局配置：`GET/PUT /system/api-config/server`。
  Future<Map<String, dynamic>> serverApiConfig({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/api-config/server',
        cancelToken: cancelToken);
    return _obj(r.data, 'server api-config');
  }

  Future<Map<String, dynamic>> saveServerApiConfig(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/api-config/server',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'server api-config save');
  }

  /// 服务器级语音：`GET/PUT /system/speech-config/server`。
  Future<Map<String, dynamic>> serverSpeechConfig({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/speech-config/server',
        cancelToken: cancelToken);
    return _obj(r.data, 'server speech config');
  }

  Future<Map<String, dynamic>> saveServerSpeechConfig(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/speech-config/server',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'server speech save');
  }

  /// 服务器级识图：`GET/PUT /system/vlm-config/server`。
  Future<Map<String, dynamic>> serverVlmConfig({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/vlm-config/server',
        cancelToken: cancelToken);
    return _obj(r.data, 'server vlm config');
  }

  Future<Map<String, dynamic>> saveServerVlmConfig(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/vlm-config/server',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'server vlm save');
  }

  /// 服务器级生图（含 daily_limit）：`GET/PUT /system/image-gen-config/server`。
  Future<Map<String, dynamic>> serverImageGenConfig({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/system/image-gen-config/server',
        cancelToken: cancelToken);
    return _obj(r.data, 'server image-gen config');
  }

  Future<Map<String, dynamic>> saveServerImageGenConfig(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put('/api/v1/system/image-gen-config/server',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'server image-gen save');
  }

  /// 服务器级任务配置：`GET /system/api-config/task/server/{task}`。
  Future<Map<String, dynamic>> serverTaskConfig(String task,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get(
        '/api/v1/system/api-config/task/server/$task',
        cancelToken: cancelToken);
    return _obj(r.data, 'server task config');
  }

  /// 写服务器级任务配置：`PUT /system/api-config/task/server/{task}`。
  Future<Map<String, dynamic>> saveServerTaskConfig(String task,
      Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.put(
        '/api/v1/system/api-config/task/server/$task',
        data: body,
        cancelToken: cancelToken);
    return _obj(r.data, 'server task save');
  }

}
