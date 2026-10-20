// 会话工作台（M1a-3）共享 core 契约测试。
//
// 守卫要点（契约权威＝backend/app/api/chat.py 与 app/schemas/chat.py）：
// - ChatSessionSummary / ChatHistoryPage 严格解析：必填缺失/类型错 ⇒ FormatException
//   （归一为 ApiException(unknown)），**不得**伪装成「成功的空列表」；
// - listSessions：路径 `GET /api/v1/chat/sessions`，**无任何 query**（接口没有角色过滤，
//   不伪造）；getMessagePage：路径 + skip/limit，且**携带 total**；
// - createSession：默认请求**不下发** force_new（与既有逐字节一致）；forceNew=true 才下发；
// - 旧 getMessages 返回类型/行为不变（兼容承诺，不因新增 total 而破坏调用方）；
// - CancelToken 真实传到适配器，取消归一为 ApiException(cancelled)。
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

(ChatTransport, List<RequestOptions>) _rig(
    Future<ResponseBody> Function(RequestOptions) handle) {
  final seen = <RequestOptions>[];
  final client = AmbApiClient();
  client.configure(baseUrl: 'http://synthetic.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter((o) {
    seen.add(o);
    return handle(o);
  });
  return (ChatTransport(client), seen);
}

Map<String, dynamic> _msg(int id, int sid, String content) => {
      'id': id,
      'session_id': sid,
      'sender_type': 'ai',
      'content': content,
      'created_at': '2026-10-16T01:00:00',
    };

void main() {
  group('会话模型解析（真实封套逐字段）', () {
    test('ChatSessionSummary：title 可空、is_active 默认 true、时间原样', () {
      final s = ChatSessionSummary.fromJson({'id': 5, 'character_id': 2});
      expect(s.id, 5);
      expect(s.characterId, 2);
      expect(s.title, isNull); // 未命名：展示层用双语占位，不伪造标题
      expect(s.isActive, true);
      expect(s.createdAt, '');
    });

    test('ChatSessionSummary：缺 id / character_id ⇒ FormatException（不伪装默认 0）', () {
      expect(() => ChatSessionSummary.fromJson({'character_id': 2}),
          throwsFormatException);
      expect(() => ChatSessionSummary.fromJson({'id': 5}), throwsFormatException);
    });

    test('chatHistoryPageFromJson：messages 必须是列表；session_id 必填；total 缺省回退本页条数',
        () {
      expect(() => chatHistoryPageFromJson({'session_id': 1, 'messages': 1}),
          throwsFormatException);
      expect(() => chatHistoryPageFromJson({'messages': []}), throwsFormatException);
      final p = chatHistoryPageFromJson(
          {'session_id': 1, 'total': 120, 'messages': [_msg(1, 1, 'x')]});
      expect(p.sessionId, 1);
      expect(p.total, 120);
      expect(p.messages.single.content, 'x');
      final fallback = chatHistoryPageFromJson(
          {'session_id': 1, 'messages': [_msg(1, 1, 'x')]});
      expect(fallback.total, 1); // total 缺失时退回本页条数（不编造更大值）
    });
  });

  group('ChatTransport 契约（路径 / query / force_new / CancelToken）', () {
    test('listSessions：GET /api/v1/chat/sessions 且无任何 query（不伪造角色过滤）', () async {
      final (t, seen) = _rig((o) async => reply({
            'sessions': [
              {'id': 1, 'character_id': 2, 'title': 'synthetic_title'},
            ],
            'total': 1,
          }));
      final list = await t.listSessions();
      expect(list.single.id, 1);
      expect(list.single.title, 'synthetic_title');
      expect(seen.single.method, 'GET');
      expect(seen.single.path, '/api/v1/chat/sessions');
      expect(seen.single.queryParameters, isEmpty);
    });

    test('getMessagePage：路径 + skip/limit + 携带 total（窗口内时间正序原样）', () async {
      final (t, seen) = _rig((o) async => reply({
            'session_id': 7,
            'total': 120,
            'messages': [_msg(51, 7, 'older_window')],
          }));
      final p = await t.getMessagePage(7, skip: 50, limit: 50);
      expect(seen.single.path, '/api/v1/chat/sessions/7/messages');
      expect(seen.single.queryParameters['skip'], 50);
      expect(seen.single.queryParameters['limit'], 50);
      expect(p.sessionId, 7);
      expect(p.total, 120);
      expect(p.messages.single.content, 'older_window');
    });

    test('createSession 默认：请求**不含** force_new（默认请求与既有逐字节一致）', () async {
      final (t, seen) = _rig((o) async => reply({'id': 101}));
      expect(await t.createSession(1), 101);
      expect(seen.single.queryParameters.keys, ['character_id']);
      expect(seen.single.queryParameters['character_id'], 1);
    });

    test('createSession forceNew=true：显式下发 force_new=true', () async {
      final (t, seen) = _rig((o) async => reply({'id': 102}));
      expect(await t.createSession(2, forceNew: true), 102);
      expect(seen.single.queryParameters['force_new'], true);
    });

    test('CancelToken 真实传到适配器：取消归一为 ApiException(cancelled)', () async {
      final cancelled = Completer<void>();
      final (t, seen) = _rig((o) {
        final c = Completer<ResponseBody>();
        o.cancelToken?.whenCancel.then((_) {
          cancelled.complete();
          if (!c.isCompleted) c.complete(reply({'sessions': []}));
        });
        return c.future;
      });
      final token = CancelToken();
      final fut = t.listSessions(cancelToken: token);
      await pumpEventQueue(); // 让请求先抵达适配器
      expect(seen.single.cancelToken, same(token));
      token.cancel('test');
      await cancelled.future;
      await expectLater(fut, throwsApi('cancelled'));
    });

    test('坏封套（缺 id）⇒ ApiException(unknown)，不得伪装成空列表', () async {
      final (t, _) = _rig((o) async => reply({'sessions': [{'character_id': 2}]}));
      // 由方法体内抛出（未经 dio）⇒ 裸 ApiException
      await expectLater(t.listSessions(), throwsBareApi('unknown'));
    });

    test('坏封套（messages 非列表）⇒ ApiException(unknown)', () async {
      final (t, _) = _rig((o) async => reply({'session_id': 1, 'messages': 'bad'}));
      await expectLater(t.getMessagePage(1), throwsBareApi('unknown'));
    });

    test('旧 getMessages 返回类型/行为不变（返回 List，不强制 total）', () async {
      final (t, seen) = _rig((o) async => reply({
            'session_id': 3,
            'messages': [_msg(1, 3, 'legacy')],
          }));
      final msgs = await t.getMessages(3);
      expect(msgs.single.content, 'legacy');
      expect(seen.single.path, '/api/v1/chat/sessions/3/messages');
      expect(seen.single.queryParameters['skip'], 0);
    });
  });
}
