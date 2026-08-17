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

  /// 市场条目详情（含 readme_text）
  Future<Map<String, dynamic>> getMarketplaceDetail(String name) async {
    final r = await dio.get('/api/v1/marketplace/$name');
    return r.data as Map<String, dynamic>;
  }

  /// 从市场安装插件（仅主账号；内置=复制示例目录 / 远程=下载 zip）
  Future<Map<String, dynamic>> installMarketplacePlugin(String name) async {
    final r = await dio.post('/api/v1/marketplace/$name/install');
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

  /// zip 安装插件（仅主账号）
  Future<Map<String, dynamic>> installPluginZip(String filePath) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: filePath.split('/').last.split('\\').last),
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
}
