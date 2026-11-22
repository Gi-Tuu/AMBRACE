import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

class AgentRunSummary {
  const AgentRunSummary({
    required this.runId,
    required this.status,
    required this.events,
  });

  final String runId;
  final String status;
  final int events;

  factory AgentRunSummary.fromJson(Map<String, dynamic> j) => AgentRunSummary(
        runId: '${j['run_id'] ?? ''}',
        status: '${j['status'] ?? ''}',
        events: (j['events'] is num) ? (j['events'] as num).toInt() : 0,
      );
}

class AgentRunsSnapshot {
  const AgentRunsSnapshot({
    required this.active,
    required this.limit,
    required this.runs,
  });

  final int active;
  final int limit;
  final List<AgentRunSummary> runs;
}

class AgentApi {
  AgentApi(this.client);

  final AmbApiClient client;

  Future<AgentRunsSnapshot> runs({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/agent/tools/runs', cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['runs'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed agent runs');
    }
    return AgentRunsSnapshot(
      active: (data['active'] is num) ? (data['active'] as num).toInt() : 0,
      limit: (data['limit'] is num) ? (data['limit'] as num).toInt() : 0,
      runs: [
        for (final x in data['runs'] as List)
          if (x is Map) AgentRunSummary.fromJson(x.cast<String, dynamic>()),
      ],
    );
  }

  /// 新建运行（`POST /api/agent/tools/runs`）。execute=false＝宿主注册式（事件由
  /// 本端聊天链路挂接）；execute=true 需 M7（未启用时后端 409 M7_DISABLED）。
  Future<Map<String, dynamic>> createRun(
      {required String task,
      String sessionId = '',
      String? runId,
      bool execute = false,
      CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/agent/tools/runs',
        data: {
          'execute': execute,
          'task': task,
          'session_id': sessionId,
          if (runId != null && runId.isNotEmpty) 'run_id': runId,
        },
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed run create');
    }
    return data.cast<String, dynamic>();
  }

  /// 单查运行状态：`GET /api/agent/tools/runs/{id}`。
  Future<Map<String, dynamic>> getRun(String runId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/agent/tools/runs/$runId',
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed run state');
    }
    return data.cast<String, dynamic>();
  }

  /// 取消运行：`POST /api/agent/tools/runs/{id}/cancel`（2s grace 收口）。
  Future<Map<String, dynamic>> cancelRun(String runId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/agent/tools/runs/$runId/cancel',
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed run cancel');
    }
    return data.cast<String, dynamic>();
  }

  /// 签发 SSE ticket（`POST /{id}/stream-ticket`）：**HttpOnly cookie**
  /// `ambrace_stream_{id}`（path 限定到该 run 的 events 路径）→
  /// `{run_id,expires_in}`。浏览器端随后用 EventSource 直连 events（cookie 自动随发）。
  Future<Map<String, dynamic>> streamTicket(String runId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/agent/tools/runs/$runId/stream-ticket',
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed stream ticket');
    }
    return data.cast<String, dynamic>();
  }
}
