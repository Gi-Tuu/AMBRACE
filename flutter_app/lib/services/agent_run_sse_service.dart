// M6 SSE 增量渲染客户端（Flutter/Dart）。
//
// 契约：docs/agent-tools-m6-sse-contract.md v1.0
// - Bearer 鉴权（浏览器 EventSource 用 cookie ticket，App 端用 Bearer 更简单）
// - Last-Event-ID/offset 续传；INVALID_OFFSET(400)/STREAM_GAP(409) 走 onProtocolError
// - 控制帧：eof / stream_lagged / ping 不进业务事件（offset 为空）
// - 断线自动重连（有限次数），重连带已收到的最大 offset
import 'dart:async';
import 'dart:convert';
import 'dart:io';

class AgentRunEvent {
  final int? offset;
  final String type;
  final Map<String, dynamic> data;

  const AgentRunEvent({this.offset, required this.type, required this.data});

  @override
  String toString() => 'AgentRunEvent(offset: $offset, type: $type)';
}

class AgentRunSseClient {
  AgentRunSseClient({
    required this.baseUrl,
    required this.token,
    this.maxRetries = 3,
    this.retryDelay = const Duration(milliseconds: 300),
  });

  final String baseUrl;
  final String token;
  final int maxRetries;
  final Duration retryDelay;

  final Map<String, int> _lastOffsets = <String, int>{};

  /// 订阅一个 run 的事件；返回时流已 EOF/终态或不可恢复错误。
  Future<void> stream(
    String runId, {
    required void Function(AgentRunEvent) onEvent,
    void Function()? onEof,
    void Function(String code, Map<String, dynamic> data)? onProtocolError,
  }) async {
    // offset 按 run 隔离：同 client 连续订阅不同 run 不得串号
    _lastOffsets[runId] = 0;
    var attempt = 0;
    while (true) {
      try {
        await _connectOnce(runId, onEvent: onEvent, onEof: onEof,
            onProtocolError: onProtocolError);
        return;
      } on _StreamClosed {
        attempt++;
        if (attempt > maxRetries) rethrow;
        await Future<void>.delayed(retryDelay);
      }
    }
  }

  Future<void> _connectOnce(
    String runId, {
    required void Function(AgentRunEvent) onEvent,
    void Function()? onEof,
    void Function(String code, Map<String, dynamic> data)? onProtocolError,
  }) async {
    final uri = Uri.parse('$baseUrl/api/agent/tools/runs/$runId/events');
    final client = HttpClient();
    try {
      final req = await client.getUrl(uri);
      req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      req.headers.set(HttpHeaders.acceptHeader, 'text/event-stream');
      final last = _lastOffsets[runId] ?? 0;
      if (last > 0) {
        req.headers.set('Last-Event-ID', '$last');
      }
      final resp = await req.close();

      if (resp.statusCode == 400 || resp.statusCode == 409) {
        final body = await utf8.decoder.bind(resp).join();
        final detail = _detailOf(body);
        onProtocolError?.call(
            detail['code']?.toString() ?? 'HTTP_${resp.statusCode}', detail);
        return;
      }
      if (resp.statusCode != 200) {
        throw HttpException('HTTP ${resp.statusCode}');
      }

      String? id;
      String? event;
      final data = StringBuffer();
      await for (final line in resp.transform(utf8.decoder).transform(const LineSplitter())) {
        if (line.isEmpty) {
          if (event != null || id != null || data.isNotEmpty) {
            final raw = data.toString();
            final parsed = _parseData(raw);
            final type = event ?? 'message';
            if (type == 'eof') {
              onEof?.call();
              return;
            }
            if (type == 'stream_lagged') {
              onProtocolError?.call('STREAM_LAGGED', parsed);
              return;
            }
            if (type == 'ping' || type == 'keepalive') {
              // 控制帧，不产生业务事件
            } else {
              final off = int.tryParse(id ?? '');
              if (off != null && off > (_lastOffsets[runId] ?? 0)) _lastOffsets[runId] = off;
              onEvent(AgentRunEvent(offset: off, type: type, data: parsed));
            }
          }
          id = null;
          event = null;
          data.clear();
          continue;
        }
        if (line.startsWith(':')) continue; // SSE 注释/ping
        if (line.startsWith('id:')) {
          id = line.substring(3).trim();
        } else if (line.startsWith('event:')) {
          event = line.substring(6).trim();
        } else if (line.startsWith('data:')) {
          data.write(line.substring(5).trim());
        }
      }
      // 连接提前关闭且未收到 eof：交给重连逻辑
      throw const _StreamClosed();
    } finally {
      client.close(force: true);
    }
  }

  Map<String, dynamic> _parseData(String raw) {
    if (raw.isEmpty) return const {};
    try {
      final v = jsonDecode(raw);
      return v is Map<String, dynamic> ? v : {'value': v};
    } catch (_) {
      return {'raw': raw};
    }
  }

  Map<String, dynamic> _detailOf(String body) {
    try {
      final v = jsonDecode(body);
      if (v is Map && v['detail'] is Map) {
        return Map<String, dynamic>.from(v['detail'] as Map);
      }
      if (v is Map) return Map<String, dynamic>.from(v);
    } catch (_) {}
    return {'code': 'UNKNOWN'};
  }
}

class _StreamClosed implements Exception {
  const _StreamClosed();
}
