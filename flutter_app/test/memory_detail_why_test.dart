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

/// RC2 G5：记忆详情「为什么记住」卡（默认折叠、只读）。
///
/// 全部合成：本地假 token + FakeAdapter，不连真实 8000、不读真实数据。
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

  Map<String, dynamic> whyJson({
    int memoryId = 1,
    bool hasSource = true,
    int receipts = 1,
    List<Map<String, dynamic>> sources = const [],
    String note = '',
  }) =>
      {
        'status': 'ok',
        'memory_id': memoryId,
        'source_event_uid': hasSource ? 'mse-1' : null,
        'has_source': hasSource,
        'sources': sources,
        'receipt_count': receipts,
        'note': note,
      };

  Map<String, dynamic> sourceJson({
    String kind = 'chat_message',
    int? sessionId = 31,
    String? excerpt = '记得带材料',
    String? eventAt = '2026-10-25T08:00:00',
  }) =>
      {
        'kind': kind,
        'source_row_id': 7,
        'session_id': sessionId,
        'event_at': eventAt,
        'message_id': 7,
        'message_excerpt': excerpt,
        'message_created_at': eventAt,
      };

  testWidgets('默认折叠：只显示摘要，不显示原文摘录', (tester) async {
    api.json('GET', '/api/v1/memories/1/why',
        whyJson(sources: [sourceJson()], receipts: 2));
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();

    expect(find.text('为什么记住'), findsOneWidget);
    expect(find.text('来源事件 1 条 · 写入回执 2 条'), findsOneWidget);
    expect(find.text('记得带材料'), findsNothing);
    expect(find.text('展开'), findsOneWidget);
  });

  testWidgets('展开后显示来源类型/时间/会话/原文摘录，可收起', (tester) async {
    api.json('GET', '/api/v1/memories/1/why', whyJson(sources: [sourceJson()]));
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('展开'));
    await tester.pumpAndSettle();
    expect(find.text('对话消息'), findsOneWidget);
    expect(find.text('2026-10-25 16:00'), findsOneWidget);
    expect(find.text('会话 31'), findsOneWidget);
    expect(find.text('原话摘录'), findsOneWidget);
    expect(find.text('记得带材料'), findsOneWidget);

    await tester.tap(find.text('收起'));
    await tester.pumpAndSettle();
    expect(find.text('记得带材料'), findsNothing);
  });

  testWidgets('无来源：明确说明，不显示展开按钮', (tester) async {
    api.json('GET', '/api/v1/memories/1/why',
        whyJson(hasSource: false, note: 'no_source_event'));
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();

    expect(find.text('为什么记住'), findsOneWidget);
    expect(find.textContaining('没有来源记录'), findsOneWidget);
    expect(find.text('展开'), findsNothing);
  });

  testWidgets('加载失败显示失败与重试；重试成功后显示来源', (tester) async {
    api.json('GET', '/api/v1/memories/1/why', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();

    expect(find.text('来源链加载失败'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('测试正文'), findsWidgets);

    api.json('GET', '/api/v1/memories/1/why',
        whyJson(sources: [sourceJson(excerpt: '重试后的原文')]));
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('来源链加载失败'), findsNothing);
    expect(find.text('来源事件 1 条 · 写入回执 1 条'), findsOneWidget);
    await tester.tap(find.text('展开'));
    await tester.pumpAndSettle();
    expect(find.text('重试后的原文'), findsOneWidget);
  });

  testWidgets('切换记忆：A 晚到的来源链不得写进 B', (tester) async {
    api.gate('GET', '/api/v1/memories/1/why');
    await tester.pumpWidget(host(_memory(1)));
    await tester.pump();

    api.json('GET', '/api/v1/memories/2/why',
        whyJson(memoryId: 2, sources: [sourceJson(excerpt: 'B 的来源原文')]));
    await tester.pumpWidget(host(_memory(2)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('展开'));
    await tester.pumpAndSettle();
    expect(find.text('B 的来源原文'), findsOneWidget);
    expect(find.text('A 的来源原文'), findsNothing);

    api.completeGate('GET', '/api/v1/memories/1/why',
        whyJson(sources: [sourceJson(excerpt: 'A 的来源原文')]));
    await tester.pumpAndSettle();
    expect(find.text('A 的来源原文'), findsNothing);
    expect(find.text('B 的来源原文'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('英文本地化：标题与摘要', (tester) async {
    api.json('GET', '/api/v1/memories/1/why',
        whyJson(sources: [sourceJson()], receipts: 2));
    await tester.pumpWidget(host(_memory(1), locale: const Locale('en')));
    await tester.pumpAndSettle();
    expect(find.text('Why it was remembered'), findsOneWidget);
    expect(find.text('1 source event(s) · 2 write receipt(s)'), findsOneWidget);
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
}
