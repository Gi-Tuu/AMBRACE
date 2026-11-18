import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/services/agent_run_sse_service.dart';

Future<HttpServer> _serve(FutureOr<void> Function(HttpRequest, int) handler) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  var count = 0;
  server.listen((req) async {
    count++;
    await handler(req, count);
  });
  return server;
}

void _sse(HttpResponse resp, String id, String event, Map<String, dynamic> data) {
  resp.write('id: $id\nevent: $event\ndata: ${jsonEncode(data)}\n\n');
}

void main() {
  test('解析事件顺序、offset 与 eof', () async {
    final server = await _serve((req, _) async {
      final resp = req.response..statusCode = 200;
      resp.headers.contentType = ContentType('text', 'event-stream');
      _sse(resp, '1', 'tool_call', {'tool': 'a'});
      _sse(resp, '2', 'tool_result', {'tool': 'a', 'ok': true});
      _sse(resp, '3', 'done', {'status': 'ok'});
      resp.write('event: eof\ndata: {}\n\n');
      await resp.close();
    });
    final client = AgentRunSseClient(
        baseUrl: 'http://127.0.0.1:${server.port}', token: 't');
    final events = <AgentRunEvent>[];
    var eof = false;
    await client.stream('run-1', onEvent: events.add, onEof: () => eof = true);
    await server.close(force: true);

    expect(events.map((e) => e.type).toList(),
        ['tool_call', 'tool_result', 'done']);
    expect(events.map((e) => e.offset).toList(), [1, 2, 3]);
    expect(eof, isTrue);
  });

  test('断线重连带 Last-Event-ID 续传', () async {
    final lastIds = <String>[];
    final server = await _serve((req, count) async {
      lastIds.add(req.headers.value('Last-Event-ID') ?? '');
      final resp = req.response..statusCode = 200;
      resp.headers.contentType = ContentType('text', 'event-stream');
      if (count == 1) {
        _sse(resp, '1', 'tool_call', {'tool': 'a'});
        await resp.flush();
        await Future<void>.delayed(const Duration(milliseconds: 80));
        await resp.close();
      } else {
        _sse(resp, '2', 'tool_result', {'tool': 'a'});
        resp.write('event: eof\ndata: {}\n\n');
        await resp.close();
      }
    });
    final client = AgentRunSseClient(
        baseUrl: 'http://127.0.0.1:${server.port}',
        token: 't',
        retryDelay: const Duration(milliseconds: 10));
    final offsets = <int?>[];
    await client.stream('run-1', onEvent: (e) => offsets.add(e.offset));
    await server.close(force: true);

    expect(offsets, [1, 2]);
    expect(lastIds.length, 2);
    expect(lastIds[1], '1');
  });

  test('STREAM_GAP 409 与 INVALID_OFFSET 400 走协议错误', () async {
    final server = await _serve((req, _) async {
      final resp = req.response;
      if (req.uri.path.contains('gap')) {
        resp.statusCode = 409;
        resp.headers.contentType = ContentType.json;
        resp.write(jsonEncode({'detail': {
          'code': 'STREAM_GAP',
          'oldest_available': 4,
        }}));
      } else {
        resp.statusCode = 400;
        resp.headers.contentType = ContentType.json;
        resp.write(jsonEncode({'detail': {'code': 'INVALID_OFFSET'}}));
      }
      await resp.close();
    });
    final client = AgentRunSseClient(
        baseUrl: 'http://127.0.0.1:${server.port}', token: 't');
    String? code;
    Map<String, dynamic>? data;
    await client.stream('gap',
        onEvent: (_) {},
        onProtocolError: (c, d) {
          code = c;
          data = d;
        });
    expect(code, 'STREAM_GAP');
    expect(data?['oldest_available'], 4);

    code = null;
    await client.stream('bad', onEvent: (_) {},
        onProtocolError: (c, _) => code = c);
    expect(code, 'INVALID_OFFSET');
    await server.close(force: true);
  });

  test('双客户端同 run 一致，慢客户端 lagged 不影响快客户端', () async {
    final server = await _serve((req, _) async {
      final resp = req.response..statusCode = 200;
      resp.headers.contentType = ContentType('text', 'event-stream');
      final isSlow = req.uri.path.contains('/runs/slow/');
      if (isSlow) {
        resp.write('event: stream_lagged\ndata: {"dropped": 5}\n\n');
        await resp.close();
        return;
      }
      for (var i = 1; i <= 3; i++) {
        _sse(resp, '$i', 'tool_call', {'tool': 't$i'});
      }
      resp.write('event: eof\ndata: {}\n\n');
      await resp.close();
    });
    final base = 'http://127.0.0.1:${server.port}';
    final fast1 = <int?>[];
    final fast2 = <int?>[];
    final c1 = AgentRunSseClient(baseUrl: base, token: 't');
    final c2 = AgentRunSseClient(baseUrl: base, token: 't');
    await Future.wait([
      c1.stream('run', onEvent: (e) => fast1.add(e.offset)),
      c2.stream('run', onEvent: (e) => fast2.add(e.offset)),
    ]);
    expect(fast1, [1, 2, 3]);
    expect(fast2, [1, 2, 3]);

    String? slowCode;
    final slow = AgentRunSseClient(baseUrl: base, token: 't');
    await slow.stream('slow', onEvent: (_) {},
        onProtocolError: (c, _) => slowCode = c);
    expect(slowCode, 'STREAM_LAGGED');
    await server.close(force: true);
  });

  test('run_terminal 终态后 eof 正常结束', () async {
    final server = await _serve((req, _) async {
      final resp = req.response..statusCode = 200;
      resp.headers.contentType = ContentType('text', 'event-stream');
      _sse(resp, '1', 'run_terminal',
          {'status': 'cancelled', 'synthetic': true, 'source': 'registry'});
      resp.write('event: eof\ndata: {}\n\n');
      await resp.close();
    });
    final client = AgentRunSseClient(
        baseUrl: 'http://127.0.0.1:${server.port}', token: 't');
    final types = <String>[];
    var eof = false;
    await client.stream('run-1',
        onEvent: (e) => types.add(e.type), onEof: () => eof = true);
    expect(types, ['run_terminal']);
    expect(eof, isTrue);
    await server.close(force: true);
  });

  test('同一 client 连续订阅两个 run 不串 offset', () async {
    final headers = <String>[];
    final server = await _serve((req, _) async {
      final runId = req.uri.pathSegments[4];
      headers.add('$runId:${req.headers.value('Last-Event-ID') ?? ''}');
      final resp = req.response..statusCode = 200;
      resp.headers.contentType = ContentType('text', 'event-stream');
      for (var i = 1; i <= 2; i++) {
        _sse(resp, '$i', 'tool_call', {'tool': 't$i'});
      }
      resp.write('event: eof\ndata: {}\n\n');
      await resp.close();
    });
    final client = AgentRunSseClient(
        baseUrl: 'http://127.0.0.1:${server.port}', token: 't');
    final a = <int?>[];
    final b = <int?>[];
    await client.stream('run-a', onEvent: (e) => a.add(e.offset));
    await client.stream('run-b', onEvent: (e) => b.add(e.offset));
    expect(a, [1, 2]);
    expect(b, [1, 2]);
    expect(headers[0], 'run-a:');
    expect(headers[1], 'run-b:');
    await server.close(force: true);
  });
}
