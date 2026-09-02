import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/services/websocket_service.dart';

import 'fake_api_adapter.dart';

/// B2/B7/B8 回归测试（2026-09-01 审查）：
/// - B7 前提：parseSseDataLine 对后端心跳注释行 ": ping" 返回 null；
/// - B2：SSE 空闲看门狗——半开 TCP（流无数据且不关）在注入的短时限内抛 receiveTimeout，
///   且持续有事件时不误杀（每事件重置）；
/// - B8：普通模式流式进行中 sendMessage 重入被拒（真实 ChatProvider + 挂起 SSE，仅一条流）。
/// CI 兼容（2026-09-02）：ChatProvider.startSession 会连真实 WebSocket（baseUrl 转 ws://），
/// 测试并发污染共享单例时可能连到随机端口炸掉——注入空实现避免真实连接。
class _NoopWs extends WebSocketService {
  @override
  void connect(String baseUrl, int sessionId,
      {String token = '', MessageCallback? onMessage}) {
    // no-op：不建立真实连接
  }
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('B7 前提：parseSseDataLine 对心跳注释行返回 null', () {
    expect(parseSseDataLine(': ping'), isNull);
    expect(parseSseDataLine(''), isNull);
    expect(parseSseDataLine('data: {"type":"done"}'), isA<Map<String, dynamic>>());
  });

  test('B2 空闲看门狗：流无数据且不关 → 注入时限内抛 receiveTimeout', () async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'tok');
    final ctrl = StreamController<Uint8List>();
    api.handle('POST', '/api/v1/chat/sessions/7/messages/stream',
        (_) => ResponseBody(ctrl.stream, 200, headers: {
              Headers.contentTypeHeader: ['text/event-stream'],
            }));

    final events = <Map<String, dynamic>>[];
    Object? caught;
    await ApiClient().streamMessage(
      7,
      'hi',
      lang: 'zh',
      onEvent: events.add,
      idleWatchdogLimit: const Duration(milliseconds: 120),
    ).catchError((Object e) {
      caught = e;
    });
    await Future<void>.delayed(const Duration(milliseconds: 300));
    await ctrl.close();
    expect(caught, isA<DioException>(), reason: '空闲看门狗应判死链');
    expect((caught! as DioException).type, DioExceptionType.receiveTimeout);
  });

  test('B2 看门狗每事件重置：持续有数据不超时', () async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'tok');
    final ctrl = StreamController<Uint8List>();
    api.handle('POST', '/api/v1/chat/sessions/7/messages/stream',
        (_) => ResponseBody(ctrl.stream, 200, headers: {
              Headers.contentTypeHeader: ['text/event-stream'],
            }));

    final events = <Map<String, dynamic>>[];
    final done = ApiClient().streamMessage(
      7,
      'hi',
      lang: 'zh',
      onEvent: events.add,
      idleWatchdogLimit: const Duration(milliseconds: 150),
    );
    // 每 50ms 一个 delta（< 150ms 看门狗时限），持续后收尾
    for (var i = 0; i < 8; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
      ctrl.add(Uint8List.fromList(utf8.encode('data: {"type":"delta","text":"x$i"}\n\n')));
    }
    ctrl.add(Uint8List.fromList(utf8.encode('data: {"type":"done"}\n\n')));
    await ctrl.close();
    await done;
    expect(events.where((e) => e['type'] == 'delta'), hasLength(8));
  });

  test('B7 心跳注释行不进事件流', () async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'tok');
    final ctrl = StreamController<Uint8List>();
    api.handle('POST', '/api/v1/chat/sessions/7/messages/stream',
        (_) => ResponseBody(ctrl.stream, 200, headers: {
              Headers.contentTypeHeader: ['text/event-stream'],
            }));

    final events = <Map<String, dynamic>>[];
    final done = ApiClient().streamMessage(
      7,
      'hi',
      lang: 'zh',
      onEvent: events.add,
      idleWatchdogLimit: const Duration(seconds: 5),
    );
    await Future<void>.delayed(const Duration(milliseconds: 20));
    ctrl.add(Uint8List.fromList(utf8.encode(': ping\n\n'))); // 后端 B7 心跳
    await Future<void>.delayed(const Duration(milliseconds: 20));
    ctrl.add(Uint8List.fromList(utf8.encode('data: {"type":"done"}\n\n')));
    await ctrl.close();
    await done;
    expect(events, hasLength(1));
    expect(events.first['type'], 'done');
  });

  test('B8 流式进行中 sendMessage 重入被拒（仅一条 SSE）', () async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    api.json('GET', '/api/v1/system/health',
        {'status': 'ok', 'timestamp': '2026-09-01T12:00:00Z'});
    api.json('POST', '/api/v1/chat/sessions', {'id': 7, 'character_id': 1});
    api.json('GET', '/api/v1/chat/sessions/7/messages', {'messages': []});
    api.json('GET', '/api/v1/characters/1/states',
        {'character_id': 1, 'mood': 50, 'anger': 5, 'fatigue': 10});
    api.json('GET', '/api/v1/scheduler/settings/1', {'mood_badge_enabled': false});
    api.json('GET', '/api/v1/chat/unread', {'count': 0});

    // 挂起的 SSE 流（不发任何事件、不 close）= 半开/慢生成
    final ctrl = StreamController<Uint8List>();
    api.handle('POST', '/api/v1/chat/sessions/7/messages/stream',
        (_) => ResponseBody(ctrl.stream, 200, headers: {
              Headers.contentTypeHeader: ['text/event-stream'],
            }));

    final chat = ChatProvider(wsService: _NoopWs());
    chat.setCharacter(AICharacter(id: 1, name: 'Alpha'));
    chat.setUserId(1);
    await chat.startSession();

    final first = chat.sendMessage('第一条'); // 进入流式并挂起
    await Future<void>.delayed(const Duration(milliseconds: 120));
    expect(chat.isStreaming, isTrue, reason: '前置：第一条消息确实处于流式挂起');

    await chat.sendMessage('第二条'); // 普通模式重入 → 守卫直接返回
    final streamPosts = api.requests
        .where((r) => r.method == 'POST' && r.path.endsWith('/messages/stream'))
        .length;
    expect(streamPosts, 1, reason: '流式进行中重入必须被拒（不产生第二条 SSE）');

    // 收尾：放行 done，让挂起的第一条完成
    ctrl.add(Uint8List.fromList(utf8.encode('data: {"type":"done"}\n\n')));
    await ctrl.close();
    await first;
    chat.dispose();
  });
}
