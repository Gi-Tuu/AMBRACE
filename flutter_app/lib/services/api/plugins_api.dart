import 'dart:convert';

import 'package:dio/dio.dart';
import '../api_client.dart';

/// PluginsApi：扩展（插件）系统领域 API（extension 挂到 ApiClient）
extension PluginsApi on ApiClient {
  /// 插件列表（含启用状态与配置；所有登录用户可读）
  Future<List<Map<String, dynamic>>> getPlugins() async {
    final r = await dio.get('/api/v1/plugins');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 启用 / 禁用 / 更新配置（仅主账号）
  Future<Map<String, dynamic>> updatePlugin(
    String name, {
    bool? enabled,
    Map<String, dynamic>? config,
  }) async {
    final r = await dio.put('/api/v1/plugins/$name', data: {
      if (enabled != null) 'enabled': enabled,
      if (config != null) 'config': config,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 市场列表（?q=搜索 &category=过滤 &installed=筛选；条目含 installed/enabled）
  Future<List<Map<String, dynamic>>> getMarketplace({
    String? q,
    String? category,
    bool? installed,
  }) async {
    final r = await dio.get('/api/v1/marketplace', queryParameters: {
      if (q != null && q.isNotEmpty) 'q': q,
      if (category != null) 'category': category,
      if (installed != null) 'installed': installed,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 市场总览（含顶层 allow_remote_install 开关，3.9）：{items, total, allow_remote_install}
  Future<Map<String, dynamic>> getMarketplaceOverview({
    String? q,
    String? category,
    bool? installed,
  }) async {
    final r = await dio.get('/api/v1/marketplace', queryParameters: {
      if (q != null && q.isNotEmpty) 'q': q,
      if (category != null) 'category': category,
      if (installed != null) 'installed': installed,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 市场条目详情（含 readme_text）
  Future<Map<String, dynamic>> getMarketplaceDetail(String name) async {
    final r = await dio.get('/api/v1/marketplace/$name');
    return r.data as Map<String, dynamic>;
  }

  /// 从市场安装插件（仅主账号；内置=复制示例目录 / 远程=下载 zip）。
  /// 3.9：manifest.permissions 非空时须携带 {consent: true, permissions: [...]}（一致才被接受）。
  Future<Map<String, dynamic>> installMarketplacePlugin(
    String name, {
    bool consent = false,
    List<String>? permissions,
  }) async {
    final r = await dio.post('/api/v1/marketplace/$name/install', data: {
      'consent': consent,
      if (permissions != null) 'permissions': permissions,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 远程市场配置（读，仅主账号）
  Future<Map<String, dynamic>> getMarketplaceConfig() async {
    final r = await dio.get('/api/v1/marketplace/config');
    return r.data as Map<String, dynamic>;
  }

  /// 远程市场配置（写，仅主账号）
  Future<Map<String, dynamic>> updateMarketplaceConfig(
    Map<String, dynamic> config,
  ) async {
    final r = await dio.put('/api/v1/marketplace/config', data: config);
    return r.data as Map<String, dynamic>;
  }

  /// 刷新远程市场索引（仅主账号；force=true 强制刷新）
  Future<Map<String, dynamic>> refreshMarketplace({bool force = false}) async {
    final r = await dio.post(
      '/api/v1/marketplace/refresh',
      queryParameters: {'force': force},
    );
    return r.data as Map<String, dynamic>;
  }

  /// zip 安装插件（仅主账号）。3.9：manifest.permissions 非空时须携带 consent=true + permissions 一致。
  Future<Map<String, dynamic>> installPluginZip(
    String filePath, {
    bool consent = false,
    List<String>? permissions,
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: filePath.split('/').last.split('\\').last),
      'consent': consent,
      'permissions': jsonEncode(permissions ?? []),
    });
    final r = await dio.post(
      '/api/v1/plugins/install',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  /// 探测本地 zip 包 manifest（只读取，不安装/不写库），返回 {name, version, permissions, source}
  Future<Map<String, dynamic>> probePluginZip(String filePath) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: filePath.split('/').last.split('\\').last),
    });
    final r = await dio.post(
      '/api/v1/plugins/probe',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// douyin_mcp：AI 生成草稿（kind=image_post|reply_comment，hint 为灵感/prompt）
  Future<Map<String, dynamic>> aiDouyinDraft(String kind, String hint) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/ai_draft', data: {
      'kind': kind,
      'hint': hint,
    });
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：待确认任务列表（图文发布 / 评论回复）
  Future<List<Map<String, dynamic>>> getDouyinPending() async {
    final r = await dio.get('/api/v1/plugins/douyin_mcp/pending');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// douyin_mcp：已确认待发布任务（发布倒计时：含剩余秒数）
  Future<List<Map<String, dynamic>>> getDouyinUpcoming() async {
    final r = await dio.get('/api/v1/plugins/douyin_mcp/upcoming');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// douyin_mcp：确认并执行任务（真实发布/回复）
  Future<Map<String, dynamic>> confirmDouyinTask(int taskId) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/confirm/$taskId');
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：拒绝任务
  Future<Map<String, dynamic>> rejectDouyinTask(int taskId) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/reject/$taskId');
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：为图文草稿上传配图（multipart）
  Future<Map<String, dynamic>> uploadDouyinImage(int taskId, String filePath) async {
    final form = FormData.fromMap({
      'task_id': taskId,
      'file': await MultipartFile.fromFile(filePath),
    });
    final r = await dio.post('/api/v1/plugins/douyin_mcp/upload_image',
        data: form,
        options: Options(contentType: 'multipart/form-data', sendTimeout: const Duration(seconds: 60), receiveTimeout: const Duration(seconds: 60)));
    return r.data as Map<String, dynamic>;
  }

  /// chat 型插件通用对话（48c）：persona 作 system prompt，BYOK 三级回退，不写记忆不建会话
  Future<Map<String, dynamic>> pluginChat(
    String name, {
    required String input,
    List<Map<String, dynamic>>? history,
    int? maxTokens,
  }) async {
    final r = await dio.post('/api/v1/plugins/$name/chat', data: {
      'input': input,
      if (history != null && history.isNotEmpty) 'history': history,
      if (maxTokens != null) 'maxTokens': maxTokens,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 插件页面托管 URL（48a）：{base}/api/v1/plugins/{name}/page/{file}（file 为包内相对路径）
  String getPluginPageUrl(String name, String file) {
    final base = baseUrl.replaceAll(RegExp(r'/+$'), '');
    return '$base/api/v1/plugins/$name/page/$file';
  }

  /// 插件桥调用（48a）：body {api, params} → {"ok": true, "data"} / {"ok": false, "error"}
  Future<Map<String, dynamic>> bridgeCall(
    String name,
    String api,
    Map<String, dynamic>? params,
  ) async {
    final r = await dio.post('/api/v1/plugins/$name/bridge', data: {
      'api': api,
      'params': params ?? {},
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 卸载插件（48a，仅主账号）：删目录 + plugin_stores 行 + 禁用
  Future<Map<String, dynamic>> uninstallPlugin(String name) async {
    final r = await dio.delete('/api/v1/plugins/$name');
    return Map<String, dynamic>.from(r.data as Map);
  }
}

/// #65：构造插件页面/图标鉴权请求头（纯函数，可单测）。
///
/// 后端 `plugin_page` 用 `get_current_user_id`（HTTPBearer），只认
/// `Authorization: Bearer <token>`；WebView `loadRequest` 与 `Image.network`
/// 裸加载会 401，必须带上该头。空 token 返回空 map（不额外污染请求头）。
/// [token] 从 ApiClient 现有登录态取（`ApiClient().token`）。
Map<String, String> pluginAuthHeaders(String token) {
  final t = token.trim();
  if (t.isEmpty) return const <String, String>{};
  return {'Authorization': 'Bearer $t'};
}
