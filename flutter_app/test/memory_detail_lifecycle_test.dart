import 'dart:async';

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

import 'fake_api_adapter.dart';

/// RC2 M3/M4：记忆详情「生命周期」卡（只读、状态一眼可见、404 整卡隐藏）。
///
/// 全部合成：本地假 token + FakeAdapter，不连真实 8000、不读真实数据。
/// 注意：详情页是 ListView 惰性构建，生命周期卡位于「为什么记住」之后，
/// 多数断言需先 scrollUntilVisible 把卡滚入视口（仓内 history/chain 测试同法）。
class _TestAdapter implements HttpClientAdapter {
  final Map<String, Object?> _immediate = {};
  final Map<String, int> _status = {};
  final Map<String, Completer<ResponseBody>> _gates = {};
  final List<String> requests = [];

  static String _key(String method, String path) =>
      '${method.toUpperCase()} ${path.replaceAll(RegExp(r'/+$'), '')}';

  void json(String method, String path, Object? data, {int status = 200}) {
    _immediate[_key(method, path)] = data;
    _status[_key(method, path)] = status;
  }

  void gate(String method, String path) {
    _gates.putIfAbsent(_key(method, path), () => Completer<ResponseBody>());
  }

  void completeGate(String method, String path, Object? data, {int status = 200}) {
    final gate = _gates[_key(method, path)];
    if (gate != null && !gate.isCompleted) {
      gate.complete(FakeApiAdapter.body(data, status));
    }
  }

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final key = _key(options.method, options.uri.path);
    requests.add(key);
    final gate = _gates[key];
    if (gate != null) return gate.future;
    if (_immediate.containsKey(key)) {
      return FakeApiAdapter.body(_immediate[key], _status[key] ?? 200);
    }
    // 未注册的路径返回 404 —— 与「flag 关／旧服务器」同形。
    return FakeApiAdapter.body({'detail': 'not found'}, 404);
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  late _TestAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = _TestAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'token-1');
    _prime(api, 1);
    _prime(api, 2);
  });

  Widget host(Memory memory, {Locale locale = const Locale('zh')}) =>
      ChangeNotifierProvider(
        create: (_) => SettingsProvider(),
        child: MaterialApp(
          locale: locale,
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

  /// 滚到生命周期卡（ListView 惰性构建，不滚不到）。
  Future<void> scrollToLifecycle(WidgetTester tester) async {
    await tester.scrollUntilVisible(
      find.text('记忆生命周期'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
  }

  Map<String, dynamic> lifecycleJson({
    int memoryId = 1,
    String status = 'active',
    int version = 3,
    int? supersededBy,
    String? validTo,
    String epistemic = 'FACT',
    bool isArchived = false,
    int sourceEventCount = 2,
    int revisionCount = 1,
    int derivedCount = 4,
    List<Map<String, dynamic>> receipts = const [],
  }) =>
      {
        'status': 'ok',
        'memory_id': memoryId,
        'version': version,
        'superseded_by': supersededBy,
        'valid_to': validTo,
        'epistemic_status': epistemic,
        'source': 'chat',
        'is_archived': isArchived,
        'source_event_uid': 'mse-1',
        'source_event_count': sourceEventCount,
        'receipts': receipts,
        'receipt_count': receipts.length,
        'revision_count': revisionCount,
        'derived_count': derivedCount,
      };

  testWidgets('现行记忆：状态徽章＋版本/修订/来源事件/派生信息行', (tester) async {
    api.json('GET', '/api/v1/memories/1/lifecycle', lifecycleJson());
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();
    await scrollToLifecycle(tester);

    expect(find.text('记忆生命周期'), findsOneWidget);
    expect(find.text('现行'), findsOneWidget);
    expect(find.text('已核实'), findsOneWidget);
    // 信息行是「标签/值」两个独立 Text（与详情页 _infoRow 同构）
    expect(find.text('版本: '), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('人工修订: '), findsOneWidget);
    expect(find.text('1'), findsOneWidget);
    expect(find.text('来源事件: '), findsOneWidget);
    expect(find.text('派生记忆: '), findsOneWidget);
    expect(find.textContaining('已被新记忆'), findsNothing);
  });

  testWidgets('superseded：琥珀徽章＋取代说明＋失效时间；回执默认折叠、可展开', (tester) async {
    api.json('GET', '/api/v1/memories/1/lifecycle', lifecycleJson(
      status: 'superseded',
      supersededBy: 42,
      validTo: '2026-10-25T08:00:00',
      receipts: [
        {'action': 'create', 'reason': 'chat', 'created_at': '2026-09-01T10:00:00'},
        {'action': 'supersede', 'reason': 'user_correction', 'created_at': '2026-10-25T08:00:00'},
      ],
    ));
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();
    await scrollToLifecycle(tester);

    expect(find.text('已被取代'), findsOneWidget);
    expect(find.text('已被新记忆 #42 取代，以新记忆为准'), findsOneWidget);
    expect(find.text('失效于 2026-10-25 16:00'), findsOneWidget);
    // 回执默认折叠：动作明细不可见
    expect(find.text('写入'), findsNothing);
    await tester.tap(find.text('展开'));
    await tester.pumpAndSettle();
    expect(find.text('写入'), findsOneWidget);
    expect(find.text('被取代'), findsOneWidget);
    expect(find.text('user_correction'), findsOneWidget);
    expect(find.text('2026-09-01 18:00'), findsOneWidget);
  });

  testWidgets('404（flag 关／旧服务器／记忆已删）⇒ 整卡隐藏，不显示失败', (tester) async {
    // id=3 未注册任何路径 ⇒ 全部 404（lifecycle 与 history/why/derivations 同形）。
    // history/why/derivations 走各自失败态（合法），lifecycle 必须「整卡隐藏」。
    await tester.pumpWidget(host(_memory(3)));
    await tester.pumpAndSettle();
    // 滚到生命周期卡**之后**的记忆链条卡：若生命周期卡在渲染，此程必然已构建其标题。
    await tester.scrollUntilVisible(
      find.text('记忆链条'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('记忆生命周期'), findsNothing);
    expect(find.text('生命周期加载失败'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('加载失败显示失败与重试；重试成功后显示状态', (tester) async {
    api.json('GET', '/api/v1/memories/1/lifecycle', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.text('生命周期加载失败'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(find.text('生命周期加载失败'), findsOneWidget);
    expect(find.text('重试'), findsWidgets);

    api.json('GET', '/api/v1/memories/1/lifecycle', lifecycleJson());
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('生命周期加载失败'), findsNothing);
    expect(find.text('现行'), findsOneWidget);
  });

  testWidgets('切换记忆：A 晚到的生命周期不得写进 B', (tester) async {
    api.gate('GET', '/api/v1/memories/1/lifecycle');
    await tester.pumpWidget(host(_memory(1)));
    await tester.pump();

    api.json('GET', '/api/v1/memories/2/lifecycle',
        lifecycleJson(memoryId: 2, status: 'superseded', supersededBy: 77));
    await tester.pumpWidget(host(_memory(2)));
    await tester.pumpAndSettle();
    await scrollToLifecycle(tester);
    expect(find.text('已被新记忆 #77 取代，以新记忆为准'), findsOneWidget);

    api.completeGate('GET', '/api/v1/memories/1/lifecycle',
        lifecycleJson(status: 'superseded', supersededBy: 42));
    await tester.pumpAndSettle();
    expect(find.text('已被新记忆 #42 取代，以新记忆为准'), findsNothing);
    expect(find.text('已被新记忆 #77 取代，以新记忆为准'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('英文本地化：标题／徽章／取代行', (tester) async {
    api.json('GET', '/api/v1/memories/1/lifecycle', lifecycleJson(
      status: 'superseded', supersededBy: 42));
    await tester.pumpWidget(host(_memory(1), locale: const Locale('en')));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Memory lifecycle'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(find.text('Memory lifecycle'), findsOneWidget);
    expect(find.text('Superseded'), findsOneWidget);
    expect(find.text('Superseded by memory #42; the newer one prevails'), findsOneWidget);
  });
}

Memory _memory(int id, {String content = '测试正文'}) => Memory(
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

void _prime(_TestAdapter api, int id) {
  api.json('GET', '/api/v1/memories/$id', _memoryJson(id));
  api.json('GET', '/api/v1/memories/$id/chain', {'status': 'ok', 'chain': []});
  api.json('GET', '/api/v1/memories/$id/history',
      {'memory_id': id, 'truncated': false, 'corrections': []});
  api.json('GET', '/api/v1/memories/$id/why',
      {'status': 'ok', 'memory_id': id, 'has_source': false, 'sources': [], 'receipt_count': 0, 'note': 'no_source_event'});
  api.json('GET', '/api/v1/memories/$id/derivations',
      {'status': 'ok', 'memory_id': id, 'parents': {'items': [], 'has_more': false, 'next_cursor': null, 'data_incomplete': false}, 'children': {'items': [], 'has_more': false, 'next_cursor': null, 'data_incomplete': false}});
  api.json('GET', '/api/v1/memories/$id/lifecycle',
      {'status': 'ok', 'memory_id': id, 'version': 0, 'is_archived': false, 'source_event_count': 0, 'receipts': [], 'receipt_count': 0, 'revision_count': 0, 'derived_count': 0});
}
