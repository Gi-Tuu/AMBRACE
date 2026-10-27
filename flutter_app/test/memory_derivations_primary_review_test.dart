import 'package:ai_companion/models/memory_derivations.dart';
import 'dart:async';
import 'dart:convert';

import 'package:ai_companion/features/memory/memory_detail_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/models/memory.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// RC2 G2：详情「派生关系」卡（两方向、分页、点击导航与身份边界）。
/// 全部合成：FIFO 假响应 + 本地假 token，不连真实 8000。
class _Err {
  final int status;
  const _Err(this.status);
}

class _DerivAdapter implements HttpClientAdapter {
  final Map<String, List<Object?>> _queues = {};
  final List<Uri> requests = [];

  void json(String path, Object? data, {int status = 200}) {
    _queues.putIfAbsent(path, () => []).add(status == 200 ? data : _Err(status));
  }

  void gate(String path) {
    _queues.putIfAbsent(path, () => []).add(Completer<ResponseBody>());
  }

  void completeGate(String path, Object? data) {
    final queue = _queues[path];
    if (queue == null) return;
    for (final entry in queue) {
      if (entry is Completer<ResponseBody> && !entry.isCompleted) {
        entry.complete(FakeResponse.json(data));
        return;
      }
    }
  }

  Completer<ResponseBody> hold(String path) {
    final result = Completer<ResponseBody>();
    _queues.putIfAbsent(path, () => []).add(result);
    return result;
  }

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options.uri);
    final path = options.uri.path;
    final queue = _queues[path];
    if (queue == null || queue.isEmpty) return FakeResponse.json({'detail': 'not found'}, 404);
    final next = queue.removeAt(0);
    if (next is Completer<ResponseBody>) return next.future;
    if (next is _Err) return FakeResponse.json({'detail': 'err'}, next.status);
    return FakeResponse.json(next);
  }

  @override
  void close({bool force = false}) {}
}

class FakeResponse {
  static ResponseBody json(Object? data, [int status = 200]) => ResponseBody.fromString(
        jsonEncode(data),
        status,
        headers: {
          Headers.contentTypeHeader: [Headers.jsonContentType],
        },
      );
}

void main() {
  late _DerivAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = _DerivAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 't1');
    _prime(api, 1);
    _prime(api, 2);
    _prime(api, 9);
  });

  Widget host(Memory memory, {Locale locale = const Locale('zh'), ThemeData? theme}) =>
      ChangeNotifierProvider(
        create: (_) => SettingsProvider(),
        child: MaterialApp(
          locale: locale,
          theme: theme,
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: MemoryDetailScreen(key: const ValueKey('detail'), memory: memory),
        ),
      );

  test('review: has_more requires usable next cursor', () {
    for (final cursor in [null, '', 'not-an-id']) {
      expect(() => DerivationDirection.fromJson(_dir([], hasMore: true, cursor: cursor)), throwsFormatException);
    }
  });

  testWidgets('review: empty unfinished direction is not complete empty state', (tester) async {
    api.json('/api/v1/memories/1/derivations',
      _payload(1, parentsDir: _dir([], hasMore: true, cursor: '20'), children: _dir([])));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('暂无关联记忆'), findsOneWidget, reason: 'only complete children direction may claim empty');
  });

  testWidgets('review: load more must not issue concurrent duplicate requests', (tester) async {
    api.json('/api/v1/memories/1/derivations',
      _payload(1, children: _dir([_item(12)], hasMore: true, cursor: '12')));
    final first = api.hold('/api/v1/memories/1/derivations');
    final second = api.hold('/api/v1/memories/1/derivations');
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('继续加载'));
    await tester.pump();
    await tester.tap(find.text('继续加载'));
    await tester.pump();
    first.complete(FakeResponse.json(_payload(1, children: [_item(13)])));
    second.complete(FakeResponse.json(_payload(1, children: [_item(14)])));
    await tester.pumpAndSettle();
    final issued = api.requests.where((uri) => uri.queryParameters['children_cursor'] == '12').length;
    expect(issued, 1, reason: 'one direction must have one active page request');
  });
}

Map<String, dynamic> _item(int id, {String? title, String content = '', bool archived = false,
    String status = 'active'}) =>
    {
      'id': id,
      'content': content.isEmpty ? '正文$id' : content,
      'title': title,
      'memory_type': 'event',
      'status': status,
      'is_archived': archived,
    };

Map<String, dynamic> _dir(List<Map<String, dynamic>> items,
        {bool hasMore = false, String? cursor, bool incomplete = false}) =>
    {'items': items, 'has_more': hasMore, 'next_cursor': cursor, 'data_incomplete': incomplete};

Map<String, dynamic> _payload(int id,
        {List<Map<String, dynamic>> parents = const [],
        Object? children,
        Map<String, dynamic>? parentsDir}) =>
    {
      'status': 'ok',
      'memory_id': id,
      'parents': parentsDir ?? _dir(parents),
      'children': children is List
          ? _dir(children.cast<Map<String, dynamic>>())
          : (children ?? _dir([])),
    };

Memory _mem(int id, {String content = '测试正文'}) => Memory(
      id: id,
      memoryType: 'event',
      content: content,
      importance: 3,
      createdAt: '2026-09-01T10:00:00',
    );

Map<String, dynamic> _memoryJson(int id, {String content = '测试正文'}) => {
      'id': id,
      'memory_type': 'event',
      'content': content,
      'importance': 3,
      'created_at': '2026-09-01T10:00:00',
    };

void _prime(_DerivAdapter api, int id) {
  api.json('/api/v1/memories/$id', _memoryJson(id));
  api.json('/api/v1/memories/$id/chain', {'status': 'ok', 'chain': []});
  api.json('/api/v1/memories/$id/history', {'memory_id': id, 'truncated': false, 'corrections': []});
}
