// 个人内容编辑（M1a-3）共享 editor 契约测试。
//
// 守卫要点（契约权威＝backend/app/api/user_content.py 与 backend/app/auth/router.py）：
// - 路径/方法/body 逐字核对：备忘 POST/PUT/DELETE；日记 POST（upsert by actor+date）与
//   **按数值 id** 的 DELETE（**不是**按日期——不得伪造不存在的端点）；
// - 日期原样透传（纯字符串 YYYY-MM-DD，零 UTC 换算）；
// - 返回行解析出 user_id（归属判定用）；坏封套归一 ApiException(unknown)；401 → unauthorized；
// - 与只读 ReaderApi 分离：本类才发写请求。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeAdapter implements HttpClientAdapter {
  final Future<ResponseBody> Function(RequestOptions) handle;
  FakeAdapter(this.handle);
  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? body,
          Future<void>? cancel) =>
      handle(o);
  @override
  void close({bool force = false}) {}
}

Matcher throwsApi(String kind, [Object? statusCode = anything]) => throwsA(
      isA<DioException>().having(
          (e) => e.error,
          'error',
          isA<ApiException>()
              .having((a) => a.kind, 'kind', kind)
              .having((a) => a.statusCode, 'statusCode', statusCode)),
    );

/// 方法体内抛出的 ApiException（未经过 dio）⇒ 断言裸 ApiException。
Matcher throwsBareApi(String kind) => throwsA(
      isA<ApiException>().having((a) => a.kind, 'kind', kind),
    );

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

(EditorApi, List<RequestOptions>) _rig(
    Future<ResponseBody> Function(RequestOptions) handle) {
  final seen = <RequestOptions>[];
  final client = AmbApiClient();
  client.configure(baseUrl: 'http://synthetic.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter((o) {
    seen.add(o);
    return handle(o);
  });
  return (EditorApi(client), seen);
}

Map<String, dynamic> _memoJson({int id = 1, int? userId = 4, String? title = 'T'}) => {
      'id': id,
      if (userId != null) 'user_id': userId,
      'title': title,
      'content': 'SYNTH_MEMO_BODY',
      'created_at': '2026-10-16T01:00:00',
      'updated_at': '2026-10-16T01:00:00',
    };

Map<String, dynamic> _diaryJson({int id = 2, int? userId = 4, String date = '2026-10-16'}) => {
      'id': id,
      if (userId != null) 'user_id': userId,
      'diary_date': date,
      'content': 'SYNTH_DIARY_BODY',
      'created_at': '${date}T01:00:00',
      'updated_at': '${date}T01:00:00',
    };

void main() {
  group('EditorApi 契约（路径 / 方法 / body）', () {
    test('getProfileId：GET /api/v1/auth/profile，取 id（不解码 JWT、不猜用户名）', () async {
      final (e, seen) = _rig((o) async => reply({'id': 42, 'username': 'synthetic'}));
      expect(await e.getProfileId(), 42);
      expect(seen.single.method, 'GET');
      expect(seen.single.path, '/api/v1/auth/profile');
    });

    test('createMemo：POST /api/v1/user/memos，body 含 title+content，返回行带 user_id', () async {
      final (e, seen) = _rig((o) async => reply(_memoJson(id: 9, userId: 4), 201));
      final m = await e.createMemo(title: 'T', content: 'C');
      expect(seen.single.method, 'POST');
      expect(seen.single.path, '/api/v1/user/memos');
      expect(seen.single.data, {'title': 'T', 'content': 'C'});
      expect(m.id, 9);
      expect(m.userId, 4); // 归属判定字段
    });

    test('updateMemo：PUT /api/v1/user/memos/{id}，只传需要改的字段', () async {
      final (e, seen) = _rig((o) async => reply(_memoJson(id: 9)));
      final m = await e.updateMemo(9, content: 'C2');
      expect(seen.single.method, 'PUT');
      expect(seen.single.path, '/api/v1/user/memos/9');
      expect(seen.single.data, {'content': 'C2'}); // title 未传 ⇒ body 不含 title
      expect(m.id, 9);
    });

    test('deleteMemo：DELETE /api/v1/user/memos/{id}', () async {
      final (e, seen) = _rig((o) async => reply({'status': 'ok'}));
      await e.deleteMemo(9);
      expect(seen.single.method, 'DELETE');
      expect(seen.single.path, '/api/v1/user/memos/9');
    });

    test('upsertUserDiary：POST /api/v1/user/diaries，日期纯字符串原样透传（零 UTC 换算）', () async {
      final (e, seen) = _rig((o) async => reply(_diaryJson(date: '2026-10-16'), 201));
      final d = await e.upsertUserDiary('2026-10-16', content: 'C');
      expect(seen.single.method, 'POST');
      expect(seen.single.path, '/api/v1/user/diaries');
      expect(seen.single.data, {'diary_date': '2026-10-16', 'content': 'C'});
      expect(d.diaryDate, '2026-10-16');
      expect(d.userId, 4);
    });

    test('deleteUserDiaryById：DELETE /api/v1/user/diaries/{数值 id}（不是日期）', () async {
      final (e, seen) = _rig((o) async => reply({'status': 'ok'}));
      await e.deleteUserDiaryById(77);
      expect(seen.single.method, 'DELETE');
      expect(seen.single.path, '/api/v1/user/diaries/77');
      expect(seen.single.path.endsWith('/2026-10-16'), isFalse); // 明确不是按日期
    });

    test('401 → ApiException(unauthorized)', () async {
      final (e, _) = _rig((o) async => reply({'detail': 'expired'}, 401));
      await expectLater(e.createMemo(content: 'C'), throwsApi('unauthorized', 401));
    });

    test('坏封套（缺 id）⇒ ApiException(unknown)，不得伪装成功', () async {
      final (e, _) = _rig((o) async => reply({'content': 'C'}));
      await expectLater(e.createMemo(content: 'C'), throwsBareApi('unknown'));
    });
  });
}
