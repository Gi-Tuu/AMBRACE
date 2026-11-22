import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.body, {this.status = 200});
  final String body;
  final int status;
  final List<RequestOptions> calls = [];

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
    calls.add(o);
    return ResponseBody.fromString(body, status,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]});
  }

  @override
  void close({bool force = false}) {}
}

AmbApiClient _client(_Adapter adapter) {
  final c = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  c.dio.httpClientAdapter = adapter;
  return c;
}

Map<String, dynamic> _cfgJson(int id, {bool shared = false}) => {
      'id': id,
      'user_id': shared ? 3 : 4,
      'name': 'CFG_$id',
      'base_url': 'https://api.example.com/v1',
      'api_key': shared ? '' : 'sk-1***abcd',
      'model': 'synth-model',
      'provider': 'synth',
      'enabled': true,
      'is_default': id == 1,
      'shared_with_subs': shared,
      'has_api_key': true,
      'is_shared': shared,
      'created_at': '2026-11-22T00:00:00',
      'updated_at': '2026-11-22T00:00:00',
    };

void main() {
  test('list 解析 items 并命中 GET /api/v1/llm-configs', () async {
    final a = _Adapter(jsonEncode({'items': [_cfgJson(1), _cfgJson(2, shared: true)]}));
    final api = ApiConfigApi(_client(a));
    final list = await api.list();
    expect(list, hasLength(2));
    expect(list[0]['name'], 'CFG_1');
    expect(list[1]['is_shared'], true);
    expect(a.calls.single.method, 'GET');
    expect(a.calls.single.path, '/api/v1/llm-configs');
  });

  test('create POST 全量 body 到集合路径', () async {
    final a = _Adapter(jsonEncode(_cfgJson(9)));
    final api = ApiConfigApi(_client(a));
    final created = await api.create({
      'name': 'N1',
      'base_url': 'https://api.example.com/v1',
      'model': 'm',
      'provider': 'p',
      'enabled': true,
      'is_default': false,
      'shared_with_subs': false,
      'api_key': 'sk-live',
    });
    expect(created['id'], 9);
    expect(a.calls.single.method, 'POST');
    expect(a.calls.single.path, '/api/v1/llm-configs');
    expect((a.calls.single.data as Map)['api_key'], 'sk-live');
  });

  test('update PUT /{id}；delete DELETE /{id}', () async {
    final a = _Adapter(jsonEncode(_cfgJson(3)));
    final api = ApiConfigApi(_client(a));
    await api.update(3, {'name': 'N2', 'enabled': false});
    expect(a.calls.last.method, 'PUT');
    expect(a.calls.last.path, '/api/v1/llm-configs/3');
    final d = _Adapter('', status: 204);
    await ApiConfigApi(_client(d)).delete(3);
    expect(d.calls.single.method, 'DELETE');
    expect(d.calls.single.path, '/api/v1/llm-configs/3');
  });

  test('setDefault / setShare 命中动作端点并带 shared body', () async {
    final a = _Adapter(jsonEncode(_cfgJson(2)));
    final api = ApiConfigApi(_client(a));
    await api.setDefault(2);
    expect(a.calls.single.method, 'POST');
    expect(a.calls.single.path, '/api/v1/llm-configs/2/default');
    await api.setShare(2, false);
    expect(a.calls.last.path, '/api/v1/llm-configs/2/share');
    expect((a.calls.last.data as Map)['shared'], false);
  });

  test('test 解析 ok/latency_ms/api_key_tail', () async {
    final a = _Adapter(jsonEncode(
        {'ok': true, 'latency_ms': 321, 'model': 'synth-model', 'api_key_tail': 'abcd'}));
    final api = ApiConfigApi(_client(a));
    final r = await api.test(1);
    expect(r['ok'], true);
    expect(r['latency_ms'], 321);
    expect(r['api_key_tail'], 'abcd');
    expect(a.calls.single.path, '/api/v1/llm-configs/1/test');
  });

  test('usage 命中 /api/v1/system/llm-usage 并透传统计', () async {
    final a = _Adapter(jsonEncode({
      'total_limit': 1000,
      'limit_source': 'global',
      'used_total': 400,
      'remaining': 600,
      'today': 10,
      'week': 100,
      'month': 300,
      'by_model': [
        {'model': 'm1', 'total': 400}
      ],
    }));
    final api = ApiConfigApi(_client(a));
    final u = await api.usage();
    expect(u['today'], 10);
    expect(u['remaining'], 600);
    expect((u['by_model'] as List).single['model'], 'm1');
    expect(a.calls.single.path, '/api/v1/system/llm-usage');
  });

  test('list 形状异常（非 items 包裹）抛 ApiException', () async {
    final a = _Adapter(jsonEncode({'wrong': []}));
    final api = ApiConfigApi(_client(a));
    await expectLater(api.list(), throwsA(isA<ApiException>()));
  });

  test('taskCatalog 解析 8 任务目录', () async {
    final a = _Adapter(jsonEncode({
      'tasks': [
        {'task': 'memory', 'name': '记忆处理', 'desc': '提取/摘要'},
        {'task': 'diary', 'name': '日记生成', 'desc': '每日日记'},
      ]
    }));
    final api = ApiConfigApi(_client(a));
    final list = await api.taskCatalog();
    expect(list, hasLength(2));
    expect(list[0]['task'], 'memory');
    expect(a.calls.single.path, '/api/v1/system/api-config/tasks');
  });

  test('getTaskConfig / putTaskConfig 命中用户级任务端点', () async {
    final a = _Adapter(jsonEncode(
        {'enabled': false, 'base_url': null, 'model': null, 'provider': null,
         'has_api_key': false, 'configured': false}));
    final api = ApiConfigApi(_client(a));
    final cfg = await api.getTaskConfig('memory');
    expect(cfg['configured'], false);
    expect(a.calls.single.path, '/api/v1/system/api-config/task/memory');
    final put = _Adapter(jsonEncode({'status': 'ok', 'task': 'memory', 'enabled': true, 'configured': true}));
    final r = await ApiConfigApi(_client(put)).putTaskConfig('memory',
        {'model': 'm2', 'enabled': true});
    expect(r['status'], 'ok');
    expect(put.calls.single.method, 'PUT');
    expect(put.calls.single.path, '/api/v1/system/api-config/task/memory');
    expect((put.calls.single.data as Map)['api_key'], isNull); // 空键不上送＝保留现值
  });

  test('updates 解析按天公告', () async {
    final a = _Adapter(jsonEncode({'days': [
      {'date': '2026-11-22', 'title': 'T1', 'items': [
        {'content': 'C1', 'reason': 'R1'}
      ]},
    ]}));
    final api = ApiConfigApi(_client(a));
    final u = await api.updates();
    expect((u['days'] as List).first['title'], 'T1');
    expect(a.calls.single.path, '/api/v1/system/updates');
  });

  test('featureFlags 解析 111 键形状并 PUT {enabled}', () async {
    final a = _Adapter(jsonEncode({'status': 'ok', 'flags': [
      {'key': 'k1', 'enabled': true, 'scope': 'server', 'user_enabled': null,
       'meta': {'title': '开关一', 'desc': 'D', 'group': 'g1', 'order': 1,
                'visible': true}},
    ]}));
    final api = ApiConfigApi(_client(a));
    final f = await api.featureFlags();
    expect(((f['flags'] as List).first)['meta']['title'], '开关一');
    final put = _Adapter(jsonEncode({'key': 'k1', 'enabled': false, 'scope': 'user'}));
    final r = await ApiConfigApi(_client(put)).setFeatureFlag('k1', false);
    expect(r['scope'], 'user');
    expect(put.calls.single.method, 'PUT');
    expect(put.calls.single.path, '/api/v1/system/feature-flags/k1');
    expect((put.calls.single.data as Map)['enabled'], false);
  });

  test('contextBudget 快照 + setTier PUT {tier}', () async {
    final a = _Adapter(jsonEncode({'tier': 'standard', 'tier_options': [
      {'key': 'standard', 'budget_tokens': 9000, 'is_current': true},
      {'key': 'max', 'budget_tokens': 18000, 'is_current': false},
    ], 'total_quota_tokens': 9000}));
    final api = ApiConfigApi(_client(a));
    final b = await api.contextBudget();
    expect(b['tier'], 'standard');
    expect(a.calls.single.path, '/api/v1/system/context-budget');
    final put = _Adapter(jsonEncode({'tier': 'max'}));
    final r = await ApiConfigApi(_client(put)).setContextBudgetTier('max');
    expect(r['tier'], 'max');
    expect(put.calls.single.method, 'PUT');
    expect(put.calls.single.path, '/api/v1/system/context-budget/tier');
    expect((put.calls.single.data as Map)['tier'], 'max');
  });

  test('testConnection 命中 payload 型测试端点', () async {
    final a = _Adapter(jsonEncode({'ok': true, 'latency_ms': 88, 'model': 'm'}));
    final api = ApiConfigApi(_client(a));
    final r = await api.testConnection(
        {'base_url': 'https://x/v1', 'api_key': '', 'model': 'm'});
    expect(r['ok'], true);
    expect(a.calls.single.method, 'POST');
    expect(a.calls.single.path, '/api/v1/system/api-config/test');
  });
}
