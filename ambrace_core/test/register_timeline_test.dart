import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.body);
  final String body;
  final List<RequestOptions> calls = [];

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
    calls.add(o);
    return ResponseBody.fromString(body, 200,
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

void main() {
  test('register POST 201 形状：昵称/受邀码仅非空才带，token 解析', () async {
    final a = _Adapter(jsonEncode({'access_token': 'SYNTH_REG_TOKEN'}));
    final t = ChatTransport(_client(a));
    final token = await t.register('u1', 'password8',
        nickname: '  昵A  ', inviteCode: 'INV123');
    expect(token, 'SYNTH_REG_TOKEN');
    expect(a.calls.single.method, 'POST');
    expect(a.calls.single.path, '/api/v1/auth/register');
    final body = a.calls.single.data as Map;
    expect(body['username'], 'u1');
    expect(body['nickname'], '昵A'); // trim 后送出
    expect(body['invite_code'], 'INV123');
  });

  test('register 昵称/受邀码全空则两键都缺省', () async {
    final a = _Adapter(jsonEncode({'token': 'T2'}));
    final t = ChatTransport(_client(a));
    final token = await t.register('u2', 'password8');
    expect(token, 'T2');
    final body = a.calls.single.data as Map;
    expect(body.containsKey('nickname'), false);
    expect(body.containsKey('invite_code'), false);
  });

  test('register 响应缺 token 报错', () async {
    final a = _Adapter(jsonEncode({'detail': 'x'}));
    final t = ChatTransport(_client(a));
    await expectLater(
        t.register('u3', 'password8'), throwsA(isA<StateError>()));
  });

  test('getTimeline 解析 days_known/items；坏形状抛 ApiException', () async {
    final a = _Adapter(jsonEncode({
      'character_id': 11,
      'character_name': 'SYN',
      'days_known': 110,
      'first_chat_at': '2026-08-05T06:11:50',
      'has_milestones': false,
      'items': [
        {'date': '2026-11-22', 'type': 'memory', 'title': 'T1',
         'desc': 'D1', 'importance': 118},
      ],
    }));
    final api = ReaderApi(_client(a));
    final tl = await api.getTimeline(11);
    expect(tl['days_known'], 110);
    expect(((tl['items'] as List).first)['type'], 'memory');
    expect(a.calls.single.path, '/api/v1/timeline/11');
    final bad = _Adapter(jsonEncode([1]));
    await expectLater(
        ReaderApi(_client(bad)).getTimeline(11), throwsA(isA<ApiException>()));
  });
}
