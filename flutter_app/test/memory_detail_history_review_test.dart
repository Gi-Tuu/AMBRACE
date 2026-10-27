import 'package:ai_companion/models/memory_history.dart';
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

  testWidgets('review: expanded chain keeps B after A arrives late', (tester) async {
    api.gate('GET', '/api/v1/memories/1/chain');
    api.json('GET', '/api/v1/memories/2/chain', {
      'status': 'ok', 'chain': [
        {'id': 22, 'content': 'CURRENT_B_CHAIN', 'memory_type': 'event'},
      ],
    });
    await tester.pumpWidget(host(_memory(1)));
    await tester.pumpAndSettle();
    await tester.pumpWidget(host(_memory(2)));
    await tester.pumpAndSettle();
    final chain = find.widgetWithText(ExpansionTile, '记忆链条');
    await tester.ensureVisible(chain);
    await tester.tap(chain);
    await tester.pumpAndSettle();
    expect(find.text('CURRENT_B_CHAIN'), findsOneWidget);
    api.completeGate('GET', '/api/v1/memories/1/chain', {
      'status': 'ok', 'chain': [
        {'id': 11, 'content': 'STALE_A_CHAIN', 'memory_type': 'event'},
      ],
    });
    await tester.pumpAndSettle();
    expect(find.text('CURRENT_B_CHAIN'), findsOneWidget);
    expect(find.text('STALE_A_CHAIN'), findsNothing);
  });

  test('review: invalid truncated cannot claim complete empty history', () {
    expect(() => MemoryHistory.fromJson({'memory_id': 1, 'corrections': [], 'truncated': 'unknown'}), throwsFormatException);
  });

  testWidgets('review: old detail A must not replace B after switch', (tester) async {
    api.gate('GET', '/api/v1/memories/1');
    await tester.pumpWidget(host(_memory(1, content: 'A initial')));
    await tester.pumpAndSettle();
    await tester.pumpWidget(host(_memory(2, content: 'B initial')));
    await tester.pumpAndSettle();
    api.completeGate('GET', '/api/v1/memories/1', _memoryJson(1, content: 'STALE_A_DETAIL'));
    await tester.pumpAndSettle();
    expect(find.text('STALE_A_DETAIL'), findsNothing);
    expect(find.text('测试正文'), findsOneWidget);
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
}
