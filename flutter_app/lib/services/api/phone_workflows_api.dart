import '../api_client.dart';

/// PhoneWorkflowsApi：手机操作工作流 API（extension 挂到 ApiClient）
extension PhoneWorkflowsApi on ApiClient {
  Future<Map<String, dynamic>> listWorkflows() async {
    final r = await dio.get('/api/v1/phone/workflows');
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createWorkflow(
    String name, {
    String? description,
    List<Map<String, dynamic>>? steps,
    Map<String, dynamic>? graph,
  }) async {
    final r = await dio.post('/api/v1/phone/workflows', data: {
      'name': name,
      'description': description,
      if (steps != null) 'steps': steps,
      if (graph != null) 'graph': graph,
    });
    return r.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateWorkflow(
    int id, {
    String? name,
    String? description,
    List<Map<String, dynamic>>? steps,
    Map<String, dynamic>? graph,
    bool? enabled,
  }) async {
    final r = await dio.put('/api/v1/phone/workflows/$id', data: {
      if (name != null) 'name': name,
      if (description != null) 'description': description,
      if (steps != null) 'steps': steps,
      if (graph != null) 'graph': graph,
      if (enabled != null) 'enabled': enabled,
    });
    return r.data as Map<String, dynamic>;
  }

  Future<void> deleteWorkflow(int id) async {
    await dio.delete('/api/v1/phone/workflows/$id');
  }

  /// 从 workflow 型插件的 config.workflow.templates 导入模板，创建用户工作流（48c 零代码）
  Future<Map<String, dynamic>> importWorkflowTemplate(
    String pluginName,
    String templateId,
  ) async {
    final r = await dio.post('/api/v1/phone/workflows/import', data: {
      'plugin_name': pluginName,
      'template_id': templateId,
    });
    return r.data as Map<String, dynamic>;
  }
}
