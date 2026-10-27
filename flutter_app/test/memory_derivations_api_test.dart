import 'dart:convert';

import 'package:ai_companion/models/memory_derivations.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

/// RC2 G2 派生关系 API 解析契约：字段完整/null、坏容器/错 memory_id、分页标志、
/// 404/401 原样抛出（不吞错），并确认查询参数（limit/cursor）真的发出。
class _RecAdapter implements HttpClientAdapter {
  final Map<String, Object?> bodies = {};
  final Map<String, int> statuses = {};
  final List<RequestOptions> requests = [];

  void json(String path, Object? data, {int status = 200}) {
    bodies[path] = data;
    statuses[path] = status;
  }

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    final path = options.uri.path;
    return ResponseBody.fromString(
      _encode(bodies[path] ?? {'detail': 'not found'}),
      statuses[path] ?? (bodies.containsKey(path) ? 200 : 404),
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  static String _encode(Object? value) {
    if (value is String) return value;
    return jsonEncode(value);
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  late _RecAdapter api;
  setUp(() {
    api = _RecAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 't');
  });

  Map<String, dynamic> item(int id, {String? title, bool archived = false, String status = 'active'}) => {
        'id': id,
        'content': '正文$id',
        'title': title,
        'memory_type': 'event',
        'status': status,
        'is_archived': archived,
        'future_unknown': {'x': 1},
      };

  Map<String, dynamic> dir(List<Map<String, dynamic>> items,
          {bool hasMore = false, String? cursor, bool incomplete = false}) =>
      {'items': items, 'has_more': hasMore, 'next_cursor': cursor, 'data_incomplete': incomplete};

  Map<String, dynamic> payload(int id,
          {Map<String, dynamic>? parents, Map<String, dynamic>? children}) =>
      {
        'status': 'ok',
        'memory_id': id,
        'parents': parents ?? dir([]),
        'children': children ?? dir([]),
        'future_unknown': true,
      };

  test('双向字段完整解析（含 null title、has_more/cursor/incomplete）', () async {
    api.json('/api/v1/memories/7/derivations', payload(7,
        parents: dir([item(1, title: '父标题')], hasMore: true, cursor: '11', incomplete: true),
        children: dir([item(12, archived: true, status: 'stale')])));
    final data = await ApiClient().getMemoryDerivations(7);
    expect(data.memoryId, 7);
    expect(data.parents.items.single.id, 1);
    expect(data.parents.items.single.title, '父标题');
    expect(data.parents.items.single.content, '正文1');
    expect(data.parents.hasMore, isTrue);
    expect(data.parents.nextCursor, '11');
    expect(data.parents.dataIncomplete, isTrue);
    expect(data.children.items.single.isArchived, isTrue);
    expect(data.children.items.single.status, 'stale');
    expect(data.children.items.single.title, isNull);
  });

  test('查询参数：limit 默认 20、parents/children 游标分别发送', () async {
    api.json('/api/v1/memories/7/derivations', payload(7));
    await ApiClient().getMemoryDerivations(7);
    expect(api.requests.single.uri.queryParameters['limit'], '20');

    await ApiClient().getMemoryDerivations(7, limit: 5, parentsCursor: '3', childrenCursor: '9');
    final q = api.requests.last.uri.queryParameters;
    expect(q['limit'], '5');
    expect(q['parents_cursor'], '3');
    expect(q['children_cursor'], '9');
  });

  test('坏容器/坏类型 → FormatException（不静默空）', () async {
    final bads = <Map<String, dynamic>>[
      {'memory_id': 7, 'parents': dir([]), 'children': 'oops'},
      {'memory_id': 7, 'parents': {'items': 'x', 'has_more': false, 'next_cursor': null, 'data_incomplete': false}, 'children': dir([])},
      {'memory_id': 7, 'parents': {'items': [], 'has_more': 1, 'next_cursor': null, 'data_incomplete': false}, 'children': dir([])},
      {'memory_id': 7, 'parents': {'items': [], 'has_more': false, 'next_cursor': 3, 'data_incomplete': false}, 'children': dir([])},
      {'memory_id': 7, 'parents': dir([{'id': '1', 'content': 'c', 'memory_type': 'event', 'status': 'active', 'is_archived': false}]), 'children': dir([])},
    ];
    for (final bad in bads) {
      api.json('/api/v1/memories/7/derivations', bad);
      await expectLater(ApiClient().getMemoryDerivations(7), throwsA(isA<FormatException>()));
    }
  });

  test('memory_id 与请求不一致 → FormatException', () async {
    api.json('/api/v1/memories/7/derivations', payload(8));
    await expectLater(ApiClient().getMemoryDerivations(7), throwsA(isA<FormatException>()));
  });

  test('404/401 原样抛出，不吞成空关联', () async {
    api.json('/api/v1/memories/7/derivations', {'detail': 'memory_not_found'}, status: 404);
    await expectLater(ApiClient().getMemoryDerivations(7),
        throwsA(isA<DioException>().having((e) => e.response?.statusCode, 'status', 404)));
    api.json('/api/v1/memories/7/derivations', {'detail': 'please_login'}, status: 401);
    await expectLater(ApiClient().getMemoryDerivations(7),
        throwsA(isA<DioException>().having((e) => e.response?.statusCode, 'status', 401)));
  });

  test('MemoryDerivations 直接解析：缺 memory_id/方向 → FormatException', () {
    expect(() => MemoryDerivations.fromJson({'parents': {}, 'children': {}}),
        throwsA(isA<FormatException>()));
    expect(() => MemoryDerivations.fromJson({'memory_id': 1, 'parents': {}}),
        throwsA(isA<FormatException>()));
  });
}
