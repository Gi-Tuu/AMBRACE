import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.body);
  final String body;

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
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
  test('getChatArchive 命中端点并透传 days（北京时区按日分组）', () async {
    final a = _Adapter(jsonEncode({
      'session_id': 5,
      'days': [
        {
          'date': '2026-11-01',
          'count': 2,
          'messages': [
            {'id': 1, 'sender_type': 'user', 'content': 'SYNTH_U',
             'image_url': null, 'created_at': '2026-11-01T10:00:00'},
            {'id': 2, 'sender_type': 'ai', 'content': 'SYNTH_A',
             'image_url': null, 'created_at': '2026-11-01T10:01:00'},
          ],
        },
        {
          'date': '2026-10-15',
          'count': 1,
          'messages': [
            {'id': 3, 'sender_type': 'ai', 'content': 'SYNTH_OLD',
             'image_url': null, 'created_at': '2026-10-15T09:00:00'},
          ],
        },
      ],
      'total_days': 2,
    }));
    final t = ChatTransport(_client(a));
    final data = await t.getChatArchive(5);
    expect(data['total_days'], 2);
    expect((data['days'] as List).first['date'], '2026-11-01');
  });

  test('getChatArchive 形状异常（非对象）抛 ApiException', () async {
    final a = _Adapter(jsonEncode([1, 2]));
    final t = ChatTransport(_client(a));
    await expectLater(t.getChatArchive(5), throwsA(isA<ApiException>()));
  });
}
