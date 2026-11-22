import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

/// 插件列表只读客户端：只暴露名称/版本/启用状态/描述，配置内容不在 PWA 展示。
class PluginApi {
  PluginApi(this.client);

  final AmbApiClient client;

  Future<List<Map<String, dynamic>>> list({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/plugins', cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed plugin list');
    }
    return [
      for (final p in data['items'] as List)
        if (p is Map) p.cast<String, dynamic>(),
    ];
  }
}
