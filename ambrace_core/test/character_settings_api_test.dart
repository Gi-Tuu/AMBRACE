// 角色设置/内心世界 core 契约测试（2026-11-24）：
// - 路径回归：getLorebook / getAgentMind 必须带角色 id（曾丢 id 拼出 `characters//xxx`）；
// - 设置读：GET /scheduler/settings/{id} 原样对象；轨迹读：memory-trace + limit；
// - 设置写：PUT 单字段 patch、空 patch 不发请求、`status=ok` 才算成功；
// - 角色级认知循环：PUT /characters/{id} 只写 cognitive_loop_enabled，id 不符不做成功。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeAdapter implements HttpClientAdapter {
  final Future<ResponseBody> Function(RequestOptions) handle;
  FakeAdapter(this.handle);
  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? body,
          Future<void>? cancel) =>
      handle(o);
  @override
  void close({bool force = false}) {}
}

/// 本地早退（空 patch）抛裸 ApiException；HTTP 失败经拦截器包进 DioException.error。
Matcher throwsApi(String kind) => throwsA(predicate((e) {
      final err = e is DioException ? e.error : e;
      return err is ApiException && err.kind == kind;
    }, 'ApiException($kind)'));

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

(AmbApiClient, List<RequestOptions>) _rig(
    Future<ResponseBody> Function(RequestOptions) handle) {
  final seen = <RequestOptions>[];
  final client = AmbApiClient();
  client.configure(baseUrl: 'http://synthetic.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter((o) {
    seen.add(o);
    return handle(o);
  });
  return (client, seen);
}

void main() {
  group('ReaderCharacter 角色级开关', () {
    test('cognitive_loop_enabled 缺字段按 true', () {
      final c = ReaderCharacter.fromJson({'id': 1, 'name': 'A'});
      expect(c.cognitiveLoopEnabled, isTrue);
    });

    test('cognitive_loop_enabled=false 原样保留', () {
      final c = ReaderCharacter.fromJson(
          {'id': 1, 'name': 'A', 'cognitive_loop_enabled': false});
      expect(c.cognitiveLoopEnabled, isFalse);
    });
  });

  group('只读路径（回归：不得丢角色 id）', () {
    test('getLorebook 命中 /characters/7/lorebook', () async {
      final (client, seen) = _rig((o) async {
        if (o.path == '/api/v1/characters/7/lorebook') {
          return reply({'items': []});
        }
        return reply({'detail': 'not_found'}, 404);
      });
      final api = ReaderApi(client);
      expect(await api.getLorebook(7), isEmpty);
      expect(seen.single.path, '/api/v1/characters/7/lorebook');
    });

    test('getAgentMind 命中 /characters/7/agent-mind', () async {
      final (client, seen) = _rig((o) async {
        if (o.path == '/api/v1/characters/7/agent-mind') {
          return reply({'running_notes': {'pinned': []}});
        }
        return reply({'detail': 'not_found'}, 404);
      });
      final api = ReaderApi(client);
      final mind = await api.getAgentMind(7);
      expect(mind['running_notes'], isA<Map>());
      expect(seen.single.path, '/api/v1/characters/7/agent-mind');
    });

    test('getProactiveSettings 原样返回设置对象', () async {
      final (client, seen) = _rig((o) async => reply({
            'character_id': 7,
            'reasoning_level': 2,
            'dnd_start': '01:30',
          }));
      final api = ReaderApi(client);
      final s = await api.getProactiveSettings(7);
      expect(seen.single.path, '/api/v1/scheduler/settings/7');
      expect(s['reasoning_level'], 2);
      expect(s['dnd_start'], '01:30');
    });

    test('getMemoryTrace 带 limit 且返回 traces', () async {
      final (client, seen) = _rig((o) async => reply({
            'character_id': 7,
            'traces': [
              {'route': 'memory_search', 'latency_ms': 12, 'steps': {}},
            ],
          }));
      final api = ReaderApi(client);
      final t = await api.getMemoryTrace(7, limit: 5);
      expect(seen.single.path, '/api/v1/characters/7/memory-trace');
      expect(seen.single.queryParameters['limit'], 5);
      expect(t['traces'], hasLength(1));
    });

    test('坏封套（数组）不得伪装成功', () async {
      final (client, _) = _rig((o) async => reply([1, 2]));
      expect(() => ReaderApi(client).getProactiveSettings(7), throwsApi('unknown'));
    });
  });

  group('CharacterSettingsApi 写入', () {
    test('单字段 PUT：body 只含改动字段，status=ok 才算成功', () async {
      final (client, seen) = _rig((o) async => reply({'status': 'ok'}));
      final api = CharacterSettingsApi(client);
      await api.updateProactive(7, {'diary_enabled': false});
      expect(seen.single.method, 'PUT');
      expect(seen.single.path, '/api/v1/scheduler/settings/7');
      expect(seen.single.data, {'diary_enabled': false});
    });

    test('空 patch 不发请求', () async {
      final (client, seen) = _rig((o) async => reply({'status': 'ok'}));
      final api = CharacterSettingsApi(client);
      expect(() => api.updateProactive(7, const {}), throwsApi('client'));
      expect(seen, isEmpty);
    });

    test('未确认封套（缺 status）不得当成功', () async {
      final (client, _) = _rig((o) async => reply({'message': 'ok?'}));
      expect(() => CharacterSettingsApi(client).updateProactive(7, {'x': true}),
          throwsApi('unknown'));
    });

    test('认知循环：只写 cognitive_loop_enabled 且 id 必须回等', () async {
      final (client, seen) = _rig((o) async => reply({'id': 7, 'name': 'A'}));
      final api = CharacterSettingsApi(client);
      await api.setCognitiveLoop(7, enabled: false);
      expect(seen.single.method, 'PUT');
      expect(seen.single.path, '/api/v1/characters/7');
      expect(seen.single.data, {'cognitive_loop_enabled': false});
    });

    test('认知循环：返回 id 不符视为未知结果', () async {
      final (client, _) = _rig((o) async => reply({'id': 8, 'name': 'A'}));
      expect(() => CharacterSettingsApi(client).setCognitiveLoop(7, enabled: true),
          throwsApi('unknown'));
    });
  });
}
