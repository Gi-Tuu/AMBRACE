import 'dart:async';
import 'dart:convert';

import 'package:ai_companion/features/memory/memory_detail_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/models/memory.dart';
import 'package:ai_companion/models/memory_derivations.dart';
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

  testWidgets('双向标题与条目（原文/状态/归档）', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, parents: [_item(11, title: '父标题', content: '父正文')],
            children: [_item(12, content: '子正文', archived: true, status: 'stale')]));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();

    expect(find.text('派生关系'), findsOneWidget);
    expect(find.text('据此形成的记忆'), findsOneWidget);
    expect(find.text('由此派生的记忆'), findsOneWidget);
    expect(find.text('父标题'), findsOneWidget);
    expect(find.text('父正文'), findsOneWidget);
    expect(find.text('子正文'), findsOneWidget);
    expect(find.text('已过期'), findsOneWidget);
    expect(find.byIcon(Icons.archive_outlined), findsOneWidget);
  });

  testWidgets('loading 明确状态，完成后出现条目', (tester) async {
    api.gate('/api/v1/memories/1/derivations');
    await tester.pumpWidget(host(_mem(1)));
    await tester.pump();
    await tester.pump();
    expect(find.text('正在加载派生关系…'), findsOneWidget);

    api.completeGate('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(12, content: '完成后子正文')]));
    await tester.pumpAndSettle();
    expect(find.text('正在加载派生关系…'), findsNothing);
    expect(find.text('完成后子正文'), findsOneWidget);
  });

  testWidgets('失败显示失败与重试，重试成功；不伪装空关联', (tester) async {
    api.json('/api/v1/memories/1/derivations', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('派生关系加载失败'), findsOneWidget);
    expect(find.text('暂无关联记忆'), findsNothing);

    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(12, content: '重试后子正文')]));
    await tester.tap(find.text('重新加载'));
    await tester.pumpAndSettle();
    expect(find.text('重试后子正文'), findsOneWidget);
    expect(find.text('派生关系加载失败'), findsNothing);
  });

  testWidgets('两方向都空 → 各显示暂无关联记忆', (tester) async {
    api.json('/api/v1/memories/1/derivations', _payload(1));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('暂无关联记忆'), findsNWidgets(2));
  });

  testWidgets('不完整与空态区分：空但 incomplete 不得显示"暂无"', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, parentsDir: _dir([], incomplete: true), children: _dir([])));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('暂无关联记忆'), findsOneWidget);       // children 完整空
    expect(find.text('关联可能不完整'), findsOneWidget);     // parents 不完整
  });

  testWidgets('继续加载：合并去重且守游标', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: '第一页子')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: '第一页子'), _item(13, content: '第二页子')])));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('第一页子'), findsOneWidget);

    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('第二页子'), findsOneWidget);
    expect(find.text('第一页子'), findsOneWidget, reason: '按 ID 去重，不重复渲染');
    expect(find.text('继续加载'), findsNothing);
    final cursorReq = api.requests.last.queryParameters['children_cursor'];
    expect(cursorReq, '12');
  });

  testWidgets('点击条目：先补完整记录再进入详情', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(9, content: '关联条目')]));
    api.json('/api/v1/memories/9', _memoryJson(9, content: '目标详情正文'));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('关联条目'));
    await tester.pumpAndSettle();
    expect(find.text('目标详情正文'), findsWidgets);
  });

  testWidgets('点击条目 404：提示失败、不导航', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(99, content: '关联条目')]));
    api.json('/api/v1/memories/99', {'detail': 'not found'}, status: 404);
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('关联条目'));
    await tester.pumpAndSettle();
    expect(find.text('打开关联记忆失败'), findsOneWidget);
    expect(find.text('关联条目'), findsOneWidget, reason: '仍在当前页');
  });

  testWidgets('A 请求后切 B：A 的派生关系晚回不得污染 B', (tester) async {
    api.gate('/api/v1/memories/1/derivations');
    await tester.pumpWidget(host(_mem(1)));
    await tester.pump();

    api.json('/api/v1/memories/2/derivations',
        _payload(2, children: [_item(21, content: 'B 的子条目')]));
    await tester.pumpWidget(host(_mem(2)));
    await tester.pumpAndSettle();
    expect(find.text('B 的子条目'), findsOneWidget);

    api.completeGate('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(11, content: 'A 的晚到子条目')]));
    await tester.pumpAndSettle();
    expect(find.text('A 的晚到子条目'), findsNothing);
    expect(find.text('B 的子条目'), findsOneWidget);
  });

  testWidgets('点击后切换记忆：晚到的详情不得导航到旧页面', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(99, content: '关联条目')]));
    api.gate('/api/v1/memories/99');
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('关联条目'));
    await tester.pump();

    await tester.pumpWidget(host(_mem(2)));
    await tester.pumpAndSettle();
    api.completeGate('/api/v1/memories/99', _memoryJson(99, content: '旧点击目标'));
    await tester.pumpAndSettle();
    expect(find.text('旧点击目标'), findsNothing);
  });

  testWidgets('请求期间换 token：失败且不展示旧账号关联', (tester) async {
    api.gate('/api/v1/memories/1/derivations');
    await tester.pumpWidget(host(_mem(1)));
    await tester.pump();

    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 't2');
    api.completeGate('/api/v1/memories/1/derivations',
        _payload(1, children: [_item(12, content: '旧账号关联')]));
    await tester.pumpAndSettle();
    expect(find.text('旧账号关联'), findsNothing);
    expect(find.text('派生关系加载失败'), findsOneWidget);
  });

  testWidgets('深色 + 360dp 英文长文：无溢出', (tester) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final longText = 'a very long derivation content sentence ' * 8;
    api.json('/api/v1/memories/1/derivations',
        _payload(1, parents: [_item(11, content: longText)],
            children: _dir([_item(12, content: longText)], incomplete: true)));
    await tester.pumpWidget(host(_mem(1), locale: const Locale('en'), theme: ThemeData.dark()));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Derivations'), findsOneWidget);
    expect(find.text('Links may be incomplete'), findsOneWidget);
  });
  testWidgets('R6 状态本地化：active/stale/superseded/未知', (tester) async {
    api.json('/api/v1/memories/1/derivations', _payload(1, children: [
      _item(31, content: 'A', status: 'active'),
      _item(32, content: 'B', status: 'stale'),
      _item(33, content: 'C', status: 'superseded'),
      _item(34, content: 'D', status: 'weird_new_status'),
    ]));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('生效中'), findsOneWidget);
    expect(find.text('已过期'), findsOneWidget);
    expect(find.text('已被取代'), findsOneWidget);
    expect(find.text('状态未知'), findsOneWidget);
  });

  test('R5 has_more 需要可用前进游标（0/超长/超 int64 都拒绝）', () {
    for (final cursor in ['0', '9' * 20, '99999999999999999999', '']) {
      expect(() => DerivationDirection.fromJson(
          {'items': [], 'has_more': true, 'next_cursor': cursor, 'data_incomplete': false}),
          throwsFormatException, reason: 'cursor=$cursor');
    }
    expect(DerivationDirection.fromJson(
        {'items': [], 'has_more': false, 'next_cursor': null, 'data_incomplete': false}).hasMore,
        isFalse);
  });

  testWidgets('R5 空页但未完：显示还有更多，不谎称暂无', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, parentsDir: _dir([], hasMore: true, cursor: '20'), children: _dir([])));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    expect(find.text('暂无关联记忆'), findsOneWidget);      // children 完整空
    expect(find.text('还有更多关联，请继续加载'), findsOneWidget);
  });

  testWidgets('R5 分页失败保留已有条目并可重试', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: '已加载子')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('已加载子'), findsOneWidget);
    expect(find.text('重新加载'), findsOneWidget);          // 失败可重试
  });

  testWidgets('R2 游标倒退：整页拒绝、保留原条目、可重试', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: 'KEEP_FIRST')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(11, content: 'BACKWARD_PAGE')], hasMore: true, cursor: '11')));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('KEEP_FIRST'), findsOneWidget);
    expect(find.text('BACKWARD_PAGE'), findsNothing);
    expect(find.text('重新加载'), findsOneWidget);
    expect(api.requests.where((u) => u.queryParameters['children_cursor'] == '12').length, 1);
  });

  testWidgets('R2 游标合法前进：坏页后重试成功并追加新条目', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: 'KEEP_FIRST')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(13, content: 'STAGNANT_PAGE')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(14, content: 'GOOD_PAGE')], hasMore: false)));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('STAGNANT_PAGE'), findsNothing);
    expect(find.text('重新加载'), findsOneWidget);

    await tester.tap(find.text('重新加载'));
    await tester.pumpAndSettle();
    expect(find.text('GOOD_PAGE'), findsOneWidget);
    expect(find.text('KEEP_FIRST'), findsOneWidget);
    expect(find.text('重新加载'), findsNothing);
    expect(find.text('继续加载'), findsNothing);
  });

  testWidgets('R2 空扫描页但游标严格前进：可继续加载不误报错', (tester) async {
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(12, content: 'KEEP_FIRST')], hasMore: true, cursor: '12')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([], hasMore: true, cursor: '15')));
    api.json('/api/v1/memories/1/derivations',
        _payload(1, children: _dir([_item(16, content: 'AFTER_SCAN')], hasMore: false)));
    await tester.pumpWidget(host(_mem(1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('重新加载'), findsNothing);
    expect(find.text('继续加载'), findsOneWidget);
    await tester.tap(find.text('继续加载'));
    await tester.pumpAndSettle();
    expect(find.text('AFTER_SCAN'), findsOneWidget);
    expect(api.requests.where((u) => u.queryParameters['children_cursor'] == '15').length, 1);
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
