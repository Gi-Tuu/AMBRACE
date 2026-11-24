// 头像上传契约（2026-11-24）：POST /api/v1/uploads/avatar → {url}，
// 再 PUT /api/v1/characters/{id} 只写 avatar_url；坏回执不伪装成功。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeAdapter implements HttpClientAdapter {
  FakeAdapter(this.handle);
  final Future<ResponseBody> Function(RequestOptions) handle;

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? body,
          Future<void>? cancel) =>
      handle(o);

  @override
  void close({bool force = false}) {}
}

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

(AvatarApi, List<RequestOptions>) rig(
    Future<ResponseBody> Function(RequestOptions) handle) {
  final seen = <RequestOptions>[];
  final client = AmbApiClient();
  client.configure(baseUrl: 'http://synthetic.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter((o) {
    seen.add(o);
    return handle(o);
  });
  return (AvatarApi(client), seen);
}

void main() {
  test('上传：POST multipart 到 /api/v1/uploads/avatar 并返回 url', () async {
    final (api, seen) =
        rig((o) async => reply({'url': '/uploads/avatars/1/a.png'}));
    final url = await api.upload(Uint8List.fromList([1, 2, 3]));
    expect(seen.single.path, '/api/v1/uploads/avatar');
    expect(seen.single.method, 'POST');
    expect(seen.single.data, isA<FormData>());
    expect(url, '/uploads/avatars/1/a.png');
  });

  test('空字节本地拒绝，不发请求', () async {
    final (api, seen) = rig((o) async => reply({'url': '/x.png'}));
    await expectLater(
        api.upload(Uint8List(0)),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'client')));
    expect(seen, isEmpty);
  });

  test('坏回执（缺 url）→ unknown', () async {
    final (api, _) = rig((o) async => reply({'ok': true}));
    await expectLater(
        api.upload(Uint8List.fromList([1])),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')));
  });

  test('写回：PUT 只带 avatar_url，id 必须回等', () async {
    final (api, seen) = rig((o) async => reply({'id': 11, 'name': 'C'}));
    await api.setAvatar(11, '/uploads/avatars/1/a.png');
    expect(seen.single.path, '/api/v1/characters/11');
    expect(seen.single.method, 'PUT');
    expect(seen.single.data, {'avatar_url': '/uploads/avatars/1/a.png'});
    final (bad, _) = rig((o) async => reply({'id': 12}));
    await expectLater(
        bad.setAvatar(11, '/x.png'),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')));
  });
}
