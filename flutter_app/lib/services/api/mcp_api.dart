import '../api_client.dart';

/// McpApi：MCP Server 管理领域 API（Phase 3，扩展页「MCP 工具」分区）。
/// 挂到 ApiClient 上（extension）。
extension McpApi on ApiClient {
  /// MCP Server 列表（含实时状态与工具数；所有登录用户可读）
  Future<List<Map<String, dynamic>>> getMcpServers() async {
    final r = await dio.get('/api/v1/mcp/servers');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 添加 MCP Server（仅主账号）
  Future<Map<String, dynamic>> createMcpServer(Map<String, dynamic> body) async {
    final r = await dio.post('/api/v1/mcp/servers', data: body);
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 修改 MCP Server 配置（仅主账号；连接相关配置变更会断开并重新发现）
  Future<Map<String, dynamic>> updateMcpServer(int id, Map<String, dynamic> body) async {
    final r = await dio.put('/api/v1/mcp/servers/$id', data: body);
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 删除 MCP Server（仅主账号；先断开连接再删配置）
  Future<Map<String, dynamic>> deleteMcpServer(int id) async {
    final r = await dio.delete('/api/v1/mcp/servers/$id');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 建立连接（仅主账号；后台已处理重连退避）
  Future<Map<String, dynamic>> connectMcpServer(int id) async {
    final r = await dio.post('/api/v1/mcp/servers/$id/connect');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 断开连接（仅主账号）
  Future<Map<String, dynamic>> disconnectMcpServer(int id) async {
    final r = await dio.post('/api/v1/mcp/servers/$id/disconnect');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 测试连接（仅主账号；试连 + 发现工具，不保存配置/状态）
  Future<Map<String, dynamic>> testMcpServer(int id) async {
    final r = await dio.post('/api/v1/mcp/servers/$id/test');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 工具列表（已连接→live，未连接→DB 缓存；每项带 risk_level + mode）
  Future<List<Map<String, dynamic>>> getMcpServerTools(int id) async {
    final r = await dio.get('/api/v1/mcp/servers/$id/tools');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 资源列表（Phase 4：uri/name/description/mimeType；已连接→live，未连接→空列表）
  Future<List<Map<String, dynamic>>> getMcpServerResources(int id) async {
    final r = await dio.get('/api/v1/mcp/servers/$id/resources');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 提示词列表（Phase 4：只读展示；已连接→live，未连接→空列表）
  Future<List<Map<String, dynamic>>> getMcpServerPrompts(int id) async {
    final r = await dio.get('/api/v1/mcp/servers/$id/prompts');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 最近 MCP 工具调用日志（Phase 4：限当前用户，默认最近 20 条）
  Future<List<Map<String, dynamic>>> getMcpCallLogs({int limit = 20}) async {
    final r = await dio.get('/api/v1/mcp/servers/logs', queryParameters: {'limit': limit});
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 设置工具权限等级（仅主账号；mode ∈ allow/ask/forbid）
  Future<Map<String, dynamic>> setMcpToolPermission(
    int id,
    String toolName,
    String mode,
  ) async {
    final enc = Uri.encodeComponent(toolName);
    final r = await dio.put('/api/v1/mcp/servers/$id/tools/$enc', data: {'mode': mode});
    return Map<String, dynamic>.from(r.data as Map);
  }
}
