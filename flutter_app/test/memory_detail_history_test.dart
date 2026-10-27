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

/// RC2 G1：记忆详情「修正历史」卡。
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

  void clearGate(String method, String path) {
    _gates.remove(_key(method, path));
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

  const memory1 = 1;
  const memory2 = 2;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = _TestAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'token-1');
    _prime(api, memory1);
    _prime(api, memory2);
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

  testWidgets('两条修正按服务顺序展示，原文不翻译', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [
        _corr(oldValue: '旧值甲原文', newValue: '新值乙原文', changedAt: '2026-10-25T08:00:00', reason: '用户更正'),
        _corr(
          seq: 2,
          oldId: 5,
          newId: 9,
          oldValue: '旧值丙原文',
          newValue: '新值丁原文',
          changedAt: '2026-10-26T01:30:00',
          reason: null,
        ),
      ],
    });
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();

    expect(find.text('修正历史'), findsOneWidget);
    expect(find.text('旧值甲原文'), findsOneWidget);
    expect(find.text('新值乙原文'), findsOneWidget);
    expect(find.text('旧值丙原文'), findsOneWidget);
    expect(find.text('新值丁原文'), findsOneWidget);
    expect(find.text('旧值'), findsNWidgets(2));
    expect(find.text('新值'), findsNWidgets(2));
    expect(find.text('用户更正'), findsOneWidget);
    expect(find.text('2026-10-25 16:00'), findsOneWidget);
    expect(find.text('时间未知'), findsNothing);
  });

  testWidgets('reason=null 显示未记录原因；changed_at=null 显示未知时间；不显示操作者', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [
        _corr(oldValue: '旧原文', newValue: '新原文', changedAt: null, reason: null),
      ],
    });
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();

    expect(find.text('未记录原因'), findsOneWidget);
    expect(find.text('时间未知'), findsOneWidget);
    expect(find.textContaining('操作者'), findsNothing);
    expect(find.textContaining('系统判断'), findsNothing);
  });

  testWidgets('naive UTC 时间按北京时间展示且跨日', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [
        _corr(oldValue: 'a1', newValue: 'b1', changedAt: '2026-10-25T20:30:00', reason: 'r'),
      ],
    });
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();
    expect(find.text('2026-10-26 04:30'), findsOneWidget);
  });

  testWidgets('空数组显示暂无修正记录', (tester) async {
    api.json('GET', '/api/v1/memories/1/history',
        {'memory_id': 1, 'truncated': false, 'corrections': []});
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();
    expect(find.text('暂无修正记录'), findsOneWidget);
    expect(find.text('仅展示部分修正记录'), findsNothing);
  });

  testWidgets('truncated=true 且空：不得谎称完整无历史', (tester) async {
    api.json('GET', '/api/v1/memories/1/history',
        {'memory_id': 1, 'truncated': true, 'corrections': []});
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();
    expect(find.text('仅展示部分修正记录'), findsOneWidget);
    expect(find.text('暂无修正记录'), findsNothing);
  });

  testWidgets('truncated=true 且有数据：数据后显示截断提示', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': true,
      'corrections': [_corr(oldValue: 'a2', newValue: 'b2', changedAt: '2026-10-25T08:00:00', reason: 'r')],
    });
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();
    expect(find.text('a2'), findsOneWidget);
    expect(find.text('仅展示部分修正记录'), findsOneWidget);
  });

  testWidgets('loading 有明确状态，完成后出现数据', (tester) async {
    api.gate('GET', '/api/v1/memories/1/history');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();
    await tester.pump();

    expect(find.text('正在加载修正历史…'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: 'a3', newValue: 'b3', changedAt: '2026-10-25T08:00:00', reason: 'r')],
    });
    await tester.pumpAndSettle();
    expect(find.text('正在加载修正历史…'), findsNothing);
    expect(find.text('a3'), findsOneWidget);
  });

  testWidgets('加载失败显示失败与重试，原正文仍可用；重试成功后显示数据', (tester) async {
    api.json('GET', '/api/v1/memories/1', _memoryJson(1, content: '原正文仍在'));
    api.json('GET', '/api/v1/memories/1/history', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_memory(memory1, content: '原正文仍在')));
    await tester.pumpAndSettle();

    expect(find.text('修正历史加载失败'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('原正文仍在'), findsWidgets, reason: '失败不能影响正文');

    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: '重试旧值', newValue: '重试新值', changedAt: '2026-10-25T08:00:00', reason: 'r')],
    });
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('重试旧值'), findsOneWidget);
    expect(find.text('修正历史加载失败'), findsNothing);
  });

  testWidgets('同 State 切换记忆：A 晚到不污染 B 的历史', (tester) async {
    api.gate('GET', '/api/v1/memories/1/history');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();

    api.json('GET', '/api/v1/memories/2/history', {
      'memory_id': 2,
      'truncated': false,
      'corrections': [_corr(oldValue: 'B旧', newValue: 'B新', changedAt: '2026-10-25T08:00:00', reason: 'rb')],
    });
    await tester.pumpWidget(host(_memory(memory2)));
    await tester.pumpAndSettle();
    expect(find.text('B旧'), findsOneWidget);
    expect(find.text('A旧'), findsNothing);

    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: 'A旧', newValue: 'A新', changedAt: '2026-10-25T08:00:00', reason: 'ra')],
    });
    await tester.pumpAndSettle();
    expect(find.text('A旧'), findsNothing, reason: 'A 晚到不得写入 B');
    expect(find.text('B旧'), findsOneWidget);
  });

  testWidgets('请求期间换 token：结果作废并显示失败，不展示旧账号历史', (tester) async {
    api.gate('GET', '/api/v1/memories/1/history');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();

    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'token-2');
    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: '旧账号值', newValue: '旧账号新值', changedAt: '2026-10-25T08:00:00', reason: 'r')],
    });
    await tester.pumpAndSettle();
    expect(find.text('旧账号值'), findsNothing);
    expect(find.text('修正历史加载失败'), findsOneWidget);
  });

  testWidgets('请求期间页面销毁：晚到结果不 setState（无异常）', (tester) async {
    api.gate('GET', '/api/v1/memories/1/history');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();

    await tester.pumpWidget(const MaterialApp(home: SizedBox.shrink()));
    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: 'a4', newValue: 'b4', changedAt: null, reason: null)],
    });
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('英文本地化：标题与旧/新标签', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: 'old text', newValue: 'new text', changedAt: '2026-10-25T08:00:00', reason: 'fixed')],
    });
    await tester.pumpWidget(host(_memory(memory1), locale: const Locale('en')));
    await tester.pumpAndSettle();
    expect(find.text('Revision history'), findsOneWidget);
    expect(find.text('Before'), findsOneWidget);
    expect(find.text('After'), findsOneWidget);
    expect(find.text('old text'), findsOneWidget);
  });

  testWidgets('窄屏 + 长旧值/新值/原因：无布局溢出', (tester) async {
    // 360dp＝常见窄屏手机宽度；320dp 会先触发**既有**重要度星星行的旧溢出（本单不改其它卡），
    // 故窄屏基准取 360，仍能验证本卡长文本换行不溢出。
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final longOld = '很长的旧值原文' * 12;
    final longNew = '很长的新值原文' * 12;
    final longReason = '原因说明' * 20;
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': true,
      'corrections': [_corr(oldValue: longOld, newValue: longNew, changedAt: null, reason: longReason)],
    });
    await tester.pumpWidget(host(_memory(memory1, content: '正文' * 40)));
    await tester.pumpAndSettle();
    expect(find.textContaining('很长的旧值原文'), findsOneWidget);
  });
  testWidgets('深色主题：历史卡正常渲染且无异常', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: '暗色旧值', newValue: '暗色新值', changedAt: '2026-10-25T08:00:00', reason: 'r')],
    });
    await tester.pumpWidget(host(_memory(memory1), theme: ThemeData.dark()));
    await tester.pumpAndSettle();
    expect(find.text('修正历史'), findsOneWidget);
    expect(find.text('暗色旧值'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('窄屏英文长文：无布局溢出', (tester) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final longOld = 'previous value in a very long english sentence ' * 6;
    final longNew = 'current value in a very long english sentence ' * 6;
    api.json('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': true,
      'corrections': [_corr(oldValue: longOld, newValue: longNew, changedAt: null, reason: 'reason ' * 20)],
    });
    await tester.pumpWidget(host(_memory(memory1), locale: const Locale('en')));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.textContaining('previous value'), findsOneWidget);
    expect(find.text('Revision history'), findsOneWidget);
  });

  testWidgets('请求期间换服务器：结果作废并失败，不展示旧服务器历史', (tester) async {
    api.gate('GET', '/api/v1/memories/1/history');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();

    ApiClient().configure(baseUrl: 'http://127.0.0.1:8', token: 'token-1');
    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: '旧服务器值', newValue: '旧服务器新值', changedAt: null, reason: null)],
    });
    await tester.pumpAndSettle();
    expect(find.text('旧服务器值'), findsNothing);
    expect(find.text('修正历史加载失败'), findsOneWidget);
  });

  testWidgets('链条 A 晚到：切换 B 后不得写进 B 的链条', (tester) async {
    api.gate('GET', '/api/v1/memories/1/chain');
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pump();

    await tester.pumpWidget(host(_memory(memory2)));
    await tester.pumpAndSettle();

    api.completeGate('GET', '/api/v1/memories/1/chain', {
      'status': 'ok',
      'chain': [
        {'id': 9, 'content': 'A_CHAIN_STALE', 'memory_type': 'event'},
      ],
    });
    await tester.pumpAndSettle();
    expect(find.text('A_CHAIN_STALE'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('原始来源 A 晚到：切换 B 后不得写入缓存/展示', (tester) async {
    api.json('GET', '/api/v1/memories/1', _memoryJson(1, content: 'A 正文')..['source'] = 'chat'..['source_id'] = 7);
    api.gate('GET', '/api/v1/chat/messages/7');
    await tester.pumpWidget(host(Memory(
      id: 1,
      memoryType: 'event',
      source: 'chat',
      sourceId: 7,
      content: 'A 正文',
      importance: 3,
      createdAt: '2026-09-01T10:00:00',
    )));
    await tester.pumpAndSettle();

    // 展开「原始内容」触发来源读（挂起）
    final tiles = find.byType(ExpansionTile);
    expect(tiles, findsWidgets);
    await tester.tap(tiles.last);
    await tester.pump();

    await tester.pumpWidget(host(_memory(memory2)));
    await tester.pumpAndSettle();

    api.completeGate('GET', '/api/v1/chat/messages/7', {'id': 7, 'content': 'STALE_SOURCE_A'});
    await tester.pumpAndSettle();
    expect(find.text('STALE_SOURCE_A'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('历史重试绑定当前记忆 ID（切到 B 后重试请求 B）', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {'detail': 'boom'}, status: 500);
    api.json('GET', '/api/v1/memories/2/history', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();
    await tester.pumpWidget(host(_memory(memory2)));
    await tester.pumpAndSettle();

    api.requests.clear();
    api.json('GET', '/api/v1/memories/2/history', {
      'memory_id': 2,
      'truncated': false,
      'corrections': [_corr(oldValue: 'B重试旧', newValue: 'B重试新', changedAt: null, reason: null)],
    });
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('GET /api/v1/memories/2/history'));
    expect(api.requests.where((r) => r == 'GET /api/v1/memories/1/history'), isEmpty,
        reason: '重试不得回到旧记忆 ID');
    expect(find.text('B重试旧'), findsOneWidget);
  });

  testWidgets('A 的重试晚到：切换 B 后完成 A 重试不得覆盖 B', (tester) async {
    api.json('GET', '/api/v1/memories/1/history', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(host(_memory(memory1)));
    await tester.pumpAndSettle();

    api.gate('GET', '/api/v1/memories/1/history');
    await tester.tap(find.text('重试'));
    await tester.pump();

    api.json('GET', '/api/v1/memories/2/history', {
      'memory_id': 2,
      'truncated': false,
      'corrections': [_corr(oldValue: 'B旧' * 2, newValue: 'B新' * 2, changedAt: null, reason: null)],
    });
    await tester.pumpWidget(host(_memory(memory2)));
    await tester.pumpAndSettle();

    api.completeGate('GET', '/api/v1/memories/1/history', {
      'memory_id': 1,
      'truncated': false,
      'corrections': [_corr(oldValue: 'A_RETRY_STALE', newValue: 'x', changedAt: null, reason: null)],
    });
    await tester.pumpAndSettle();
    expect(find.text('A_RETRY_STALE'), findsNothing);
    expect(find.text('B旧B旧'), findsOneWidget);
  });

}

Map<String, dynamic> _corr({
  int seq = 1,
  int oldId = 3,
  int newId = 7,
  required String oldValue,
  required String newValue,
  String? changedAt,
  String? reason,
}) =>
    {
      'seq': seq,
      'old_id': oldId,
      'new_id': newId,
      'old_value': oldValue,
      'new_value': newValue,
      'changed_at': changedAt,
      'changed_at_from': changedAt == null ? null : 'valid_to',
      'old_source': 'chat',
      'new_source': 'chat',
      'reason': reason,
      'actor': null,
    };

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
}
