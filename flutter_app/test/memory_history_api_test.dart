import 'package:ai_companion/models/memory_history.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_api_adapter.dart';

/// RC2 G1：`GET /api/v1/memories/{id}/history` 的解析契约。
///
/// 重点：真实字段完整解析、null 不伪造、空数组≠失败、404/401 原样抛出、
/// 坏形状/缺容器/错 memory_id 必须抛 [FormatException]（不得降级成「暂无记录」）。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  Map<String, dynamic> correction({
    Object? changedAt = '2026-10-25T08:00:00',
    Object? reason = '用户更新',
    Object? actor,
    Object? oldSource = 'chat',
    Object? newSource = 'chat',
  }) =>
      {
        'seq': 1,
        'old_id': 3,
        'new_id': 7,
        'old_value': '旧值原文 A',
        'new_value': '新值原文 B',
        'changed_at': changedAt,
        'changed_at_from': 'valid_to',
        'old_source': oldSource,
        'new_source': newSource,
        'reason': reason,
        'actor': actor,
        'future_unknown_field': {'x': 1},
      };

  test('真实字段完整解析（含 actor=null，未知字段向前兼容）', () async {
    api.json('GET', '/api/v1/memories/7/history', {
      'status': 'ok',
      'memory_id': 7,
      'fact': {'id': 7, 'content': '当前正文', 'status': 'active', 'source': 'chat'},
      'corrections': [correction()],
      'truncated': true,
    });
    final history = await ApiClient().getMemoryHistory(7);
    expect(history.memoryId, 7);
    expect(history.truncated, isTrue);
    expect(history.corrections, hasLength(1));
    final c = history.corrections.single;
    expect(c.seq, 1);
    expect(c.oldId, 3);
    expect(c.newId, 7);
    expect(c.oldValue, '旧值原文 A');
    expect(c.newValue, '新值原文 B');
    expect(c.changedAt, '2026-10-25T08:00:00');
    expect(c.changedAtFrom, 'valid_to');
    expect(c.oldSource, 'chat');
    expect(c.newSource, 'chat');
    expect(c.reason, '用户更新');
    expect(c.actor, isNull, reason: '没有可靠 actor 不得补造');
  });

  test('可选字段为 null 正常保留为 null（不伪造时间/原因/来源）', () async {
    api.json('GET', '/api/v1/memories/7/history', {
      'memory_id': 7,
      'corrections': [correction(changedAt: null, reason: null, oldSource: null, newSource: null)],
      'truncated': false,
    });
    final c = (await ApiClient().getMemoryHistory(7)).corrections.single;
    expect(c.changedAt, isNull);
    expect(c.reason, isNull);
    expect(c.oldSource, isNull);
    expect(c.newSource, isNull);
    expect(c.actor, isNull);
  });

  test('空数组是合法空历史，不是失败', () async {
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': [], 'truncated': false});
    final history = await ApiClient().getMemoryHistory(7);
    expect(history.corrections, isEmpty);
    expect(history.truncated, isFalse);
  });

  test('404 原样抛出 DioException（不能被吞成空历史）', () async {
    api.json('GET', '/api/v1/memories/7/history', {'detail': 'memory_not_found'}, status: 404);
    await expectLater(
      ApiClient().getMemoryHistory(7),
      throwsA(isA<DioException>()
          .having((e) => e.response?.statusCode, 'status', 404)),
    );
  });

  test('401 原样抛出（交由既有认证处理）', () async {
    api.json('GET', '/api/v1/memories/7/history', {'detail': 'unauthorized'}, status: 401);
    await expectLater(
      ApiClient().getMemoryHistory(7),
      throwsA(isA<DioException>()
          .having((e) => e.response?.statusCode, 'status', 401)),
    );
  });

  test('corrections 不是数组 → FormatException', () async {
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': 'oops', 'truncated': false});
    await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()));
  });

  test('缺 corrections 容器 → FormatException', () async {
    api.json('GET', '/api/v1/memories/7/history', {'memory_id': 7, 'truncated': false});
    await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()));
  });

  test('memory_id 与请求不一致 → FormatException', () async {
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 8, 'corrections': [], 'truncated': false});
    await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()));
  });

  test('单条修正缺 new_value → FormatException', () async {
    final bad = correction()..remove('new_value');
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': [bad], 'truncated': false});
    await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()));
  });


  test('truncated 是必需 bool：缺失/null/字符串/数字都报错（R2）', () async {
    for (final bad in <Object?>[null, 'unknown', 1, 0]) {
      api.json('GET', '/api/v1/memories/7/history',
          {'memory_id': 7, 'corrections': [], 'truncated': bad});
      await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()),
          reason: 'truncated=$bad 不得归 false 冒充完整历史');
    }
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': []});
    await expectLater(ApiClient().getMemoryHistory(7), throwsA(isA<FormatException>()),
        reason: '缺 truncated 同样不合法');
  });

  test('truncated 为合法 bool 时正常解析（false 空历史 / true 截断）', () async {
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': [], 'truncated': false});
    expect((await ApiClient().getMemoryHistory(7)).truncated, isFalse);
    api.json('GET', '/api/v1/memories/7/history',
        {'memory_id': 7, 'corrections': [], 'truncated': true});
    expect((await ApiClient().getMemoryHistory(7)).truncated, isTrue);
  });

  test('MemoryHistory 直接解析：memory_id 缺失 → FormatException', () {
    expect(() => MemoryHistory.fromJson({'corrections': []}), throwsA(isA<FormatException>()));
    expect(
      () => MemoryCorrection.listFromJson(null),
      throwsA(isA<FormatException>()),
    );
  });
}
