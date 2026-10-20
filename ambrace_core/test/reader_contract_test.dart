// 只读工作台（M1a-2）共享 reader 契约测试：
// - 模型：真实后端封套逐字段解析；必填缺失抛 FormatException（不得伪装空列表）；
//   可空/未知枚举原样保留（不升格、不编造）；diary_date 纯日期字符串零时区换算。
// - API：路径与 query 参数逐字核对（不伪造接口不支持的过滤，如 moments 无 character_id）；
//   CancelToken 真实传递；错误归一（401→unauthorized、坏封套→unknown）。
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

/// 错误归一后的落点：AmbApiClient 拦截器把 ApiException 包进 DioException.error。
Matcher throwsApi(String kind, [Object? statusCode = anything]) => throwsA(
      isA<DioException>().having(
          (e) => e.error,
          'error',
          isA<ApiException>()
              .having((a) => a.kind, 'kind', kind)
              .having((a) => a.statusCode, 'statusCode', statusCode)),
    );

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

/// 造一个注入假适配器的客户端；返回 (api, 已见请求记录)。
(ReaderApi, List<RequestOptions>) _rig(
    Future<ResponseBody> Function(RequestOptions) handle) {
  final seen = <RequestOptions>[];
  final client = AmbApiClient();
  client.configure(baseUrl: 'http://synthetic.local', token: 'SYNTH_TOKEN');
  client.dio.httpClientAdapter = FakeAdapter((o) {
    seen.add(o);
    return handle(o);
  });
  return (ReaderApi(client), seen);
}

void main() {
  group('Reader 模型解析（真实封套逐字段）', () {
    test('角色详情：白名单字段解析，内部字段不进入 DTO', () {
      final c = ReaderCharacter.fromJson({
        'id': 7,
        'name': '小暖',
        'bio': '简介',
        'self_statement': '自述',
        'personality': '温柔',
        'relationship_summary': '青梅竹马',
        'current_status': '在线',
        'gender': 'female',
        'birthday': '2001-03-05',
        'height': 165,
        'weight': 50,
        'appearance': '长发',
        'talkativeness': 3,
        'timezone_offset': 8,
        'avatar_url': '/static/a.png',
        'created_at': '2026-10-01T02:03:04',
        // 以下内部字段必须被忽略（展示白名单之外）
        'system_prompt': 'INTERNAL',
        'user_llm_config_id': 99,
        'cognitive_loop_enabled': true,
        'chat_style': 'x',
        'greeting_message': 'x',
        'user_id': 1,
      });
      expect(c.id, 7);
      expect(c.name, '小暖');
      expect(c.selfStatement, '自述');
      expect(c.relationshipSummary, '青梅竹马');
      expect(c.height, 165);
      expect(c.talkativeness, 3);
      expect(c.timezoneOffset, 8);
      expect(c.createdAt, '2026-10-01T02:03:04');
    });

    test('记忆：全字段 + 中文来源标签原文 + 未知 epistemic 原样保留不升格', () {
      final m = ReaderMemory.fromJson({
        'id': 11,
        'character_id': 7,
        'memory_type': 'event',
        'sub_type': 'fact',
        'source': 'chat',
        'source_label': '对话提取',
        'epistemic_status': 'SOMETHING_NEW', // 未知枚举：原样，不升格不吞
        'title': null,
        'content': '用户喜欢雨天',
        'importance': 4,
        'importance_pct': 80.0,
        'is_pinned': true,
        'is_locked': false,
        'is_archived': false,
        'chain_id': 'c-1',
        'node_type': 'root',
        'why_it_matters': '影响心情',
        'created_at': '2026-10-02T01:00:00',
        'updated_at': '2026-10-03T01:00:00',
      });
      expect(m.id, 11);
      expect(m.sourceLabel, '对话提取');
      expect(m.epistemicStatus, 'SOMETHING_NEW');
      expect(m.importance, 4);
      expect(m.importancePct, 80.0);
      expect(m.isPinned, isTrue);
      expect(m.title, isNull);
    });

    test('必填字段缺失抛 FormatException（坏数据不得伪装成功）', () {
      expect(() => ReaderMemory.fromJson({'id': 1}),
          throwsA(isA<FormatException>())); // 缺 content
      expect(() => ReaderCharacter.fromJson({'name': 'x'}),
          throwsA(isA<FormatException>())); // 缺 id
      expect(() => ReaderDiaryEntry.fromJson({'id': 1, 'content': 'x'}),
          throwsA(isA<FormatException>())); // 缺 diary_date
      expect(() => ReaderMoment.fromJson({'id': 1}),
          throwsA(isA<FormatException>())); // 缺 content
    });

    test('链条节点：created_at 为后端 str[:19] 原文，parent_id 可空', () {
      final n = ReaderMemoryChainNode.fromJson({
        'id': 5,
        'content': '旧版本内容',
        'memory_type': 'event',
        'sub_type': null,
        'node_type': 'child',
        'parent_id': null,
        'chain_id': 'c-1',
        'created_at': '2026-10-01 08:00:00', // str[:19]，无时区后缀
        'is_current': false,
        'is_archived': true,
      });
      expect(n.createdAt, '2026-10-01 08:00:00');
      expect(n.parentId, isNull);
      expect(n.isArchived, isTrue);
    });

    test('事实历史：corrections 逐字段，reason/changed_at 为 null 时容忍', () {
      final h = ReaderFactHistory(
        factContent: '当前值',
        factStatus: 'active',
        factEpistemic: 'FACT',
        corrections: [
          ReaderFactCorrection.fromJson({
            'seq': 1,
            'old_id': 3,
            'new_id': 9,
            'old_value': '旧值',
            'new_value': '当前值',
            'changed_at': null,
            'changed_at_from': null,
            'old_source': 'chat',
            'new_source': 'chat',
            'reason': null, // 无回执 → null，不编造
            'actor': null,
          }),
        ],
        truncated: true,
      );
      expect(h.factContent, '当前值');
      expect(h.corrections.single.seq, 1);
      expect(h.corrections.single.oldValue, '旧值');
      expect(h.corrections.single.changedAt, isNull);
      expect(h.corrections.single.reason, isNull);
      expect(h.truncated, isTrue);
    });

    test('角色日记：diary_date 纯日期字符串原样（含闰日，零时区换算）', () {
      final e = ReaderDiaryEntry.fromJson({
        'id': 2,
        'character_id': 7,
        'diary_date': '2024-02-29',
        'content': '今天下雨了',
        'created_at': '2024-02-29T15:00:00',
      });
      expect(e.diaryDate, '2024-02-29');
      expect(e.content, '今天下雨了');
    });

    test('动态：嵌套评论树 + likers + isAi/authorTzOffset 语义', () {
      final m = ReaderMoment.fromJson({
        'id': 1,
        'character_id': 7,
        'character_name': '小暖',
        'user_id': 42,
        'sender_type': 'ai',
        'content': '今天的云很好看',
        'image_desc': '一张云的描述',
        'likes_count': 2,
        'created_at': '2026-10-10T10:00:00',
        'author_tz_offset': 8,
        'liked_by_me': true,
        'likers': ['我', '小暖'],
        'comments': [
          {
            'id': 10,
            'moment_id': 1,
            'parent_id': null,
            'sender_type': 'user',
            'sender_id': 42,
            'sender_name': '我',
            'content': '确实好看',
            'created_at': '2026-10-10T10:01:00',
            'replies': [
              {
                'id': 11,
                'moment_id': 1,
                'parent_id': 10,
                'sender_type': 'ai',
                'sender_id': 7,
                'sender_name': '小暖',
                'content': '对吧对吧',
                'created_at': '2026-10-10T10:02:00',
                'replies': [],
              }
            ],
          }
        ],
      });
      expect(m.isAi, isTrue);
      expect(m.likers, ['我', '小暖']);
      expect(m.comments.single.replies.single.senderName, '小暖');
      expect(m.comments.single.replies.single.parentId, 10);

      // 用户发动态：isAi=false；author_tz_offset 缺省回 8
      final u = ReaderMoment.fromJson({
        'id': 2,
        'character_id': 0,
        'character_name': '我',
        'user_id': 42,
        'sender_type': 'user',
        'content': '我发的',
      });
      expect(u.isAi, isFalse);
      expect(u.authorTzOffset, 8);
    });

    test('备忘：title 可空；个人日记：diary_date 必填', () {
      final memo = ReaderMemo.fromJson({
        'id': 1,
        'title': null,
        'content': '记得买牛奶',
        'created_at': '2026-10-01T00:00:00',
        'updated_at': '2026-10-01T01:00:00',
      });
      expect(memo.title, isNull);
      expect(
          () => ReaderUserDiary.fromJson({'id': 1, 'content': 'x'}),
          throwsA(isA<FormatException>()));
    });
  });

  group('ReaderApi 契约（路径/query/错误/CancelToken）', () {
    test('listMemories：query 逐字精确 + total 为过滤后全量（真分页口径）', () async {
      final (api, seen) = _rig((o) async => reply({
            'memories': [
              {'id': 1, 'character_id': 7, 'memory_type': 'event', 'content': 'a'},
              {'id': 2, 'character_id': 7, 'memory_type': 'event', 'content': 'b'},
            ],
            'total': 57, // 全量 57，本页 2
          }));
      final page = await api.listMemories(
          characterId: 7, memoryType: 'event', skip: 40, limit: 20);
      final q = seen.single.queryParameters;
      expect(seen.single.path, '/api/v1/memories');
      expect(q['character_id'], 7);
      expect(q['memory_type'], 'event');
      expect(q['skip'], 40);
      expect(q['limit'], 20);
      expect(q['read_only'], true, reason: '只读面必须显式走无维护读取');
      expect(page.items.length, 2);
      expect(page.total, 57);
    });

    test('listMemories：可选参数缺省时不出现在 query 里', () async {
      final (api, seen) = _rig((o) async => reply({'memories': [], 'total': 0}));
      await api.listMemories();
      final q = seen.single.queryParameters;
      expect(q.containsKey('character_id'), isFalse);
      expect(q.containsKey('memory_type'), isFalse);
      expect(q['skip'], 0);
      expect(q['limit'], 50);
      expect(q['read_only'], true); // 缺省即无维护读取，不得回退默认维护路径
    });

    test('listMemories：readOnly=false 时才显式退回默认（有维护）读取', () async {
      final (api, seen) = _rig((o) async => reply({'memories': [], 'total': 0}));
      await api.listMemories(readOnly: false);
      expect(seen.single.queryParameters['read_only'], false);
    });

    test('listMoments：只带 skip/limit（无 character_id——接口本身不支持，不得伪造）；'
        'total=本页条数', () async {
      final (api, seen) = _rig((o) async => reply({
            'moments': [
              {'id': 1, 'character_id': 7, 'character_name': '小暖', 'user_id': 1,
               'sender_type': 'ai', 'content': 'x'},
            ],
            'total': 1, // 后端口径：本页条数，不是全量
          }));
      final page = await api.listMoments(skip: 20, limit: 20);
      final q = seen.single.queryParameters;
      expect(seen.single.path, '/api/v1/moments');
      expect(q.keys.toSet(), {'skip', 'limit'});
      expect(page.pageCount, 1);
      expect(page.items.single.isAi, isTrue);
    });

    test('其余端点：路径与封套键逐字核对', () async {
      final calls = <String>[];
      final (api, seen) = _rig((o) async {
        calls.add(o.path);
        if (o.path == '/api/v1/characters/7') return reply({'id': 7, 'name': '小暖'});
        if (o.path == '/api/v1/memories/11') {
          return reply({'id': 11, 'content': 'x'});
        }
        if (o.path == '/api/v1/memories/11/chain') {
          return reply({'status': 'ok', 'chain': [
            {'id': 11, 'content': 'x', 'memory_type': 'event',
             'created_at': '2026-10-01 00:00:00', 'is_current': true,
             'is_archived': false},
          ]});
        }
        if (o.path == '/api/v1/memories/11/history') {
          return reply({'status': 'ok', 'memory_id': 11,
            'fact': {'id': 11, 'content': '当前', 'status': 'active',
                     'epistemic_status': 'FACT'},
            'corrections': [], 'truncated': false});
        }
        if (o.path == '/api/v1/diary/7') {
          return reply({'entries': [
            {'id': 1, 'character_id': 7, 'diary_date': '2026-10-01', 'content': '晴'},
          ], 'total': 1});
        }
        if (o.path == '/api/v1/diary/7/dates') {
          expect(o.queryParameters['month'], '2026-10');
          return reply({'dates': ['2026-10-01', '2026-10-03']});
        }
        if (o.path == '/api/v1/moments/1/comments') {
          return reply({'comments': [], 'total': 0});
        }
        if (o.path == '/api/v1/user/memos') {
          return reply({'memos': [
            {'id': 1, 'title': 't', 'content': 'c'},
          ], 'total': 1});
        }
        if (o.path == '/api/v1/user/diaries') {
          return reply({'diaries': [
            {'id': 1, 'diary_date': '2026-10-01', 'content': '我的日记'},
          ], 'total': 1});
        }
        if (o.path == '/api/v1/user/diaries/2026-10-01') {
          return reply({'id': 1, 'diary_date': '2026-10-01', 'content': '我的日记'});
        }
        throw StateError('unexpected ${o.path}');
      });
      await api.getCharacter(7);
      await api.getMemory(11);
      await api.getMemoryChain(11);
      await api.getMemoryHistory(11);
      await api.listCharacterDiary(7);
      await api.getDiaryDates(7, '2026-10');
      await api.getMomentComments(1);
      await api.listMemos();
      await api.listUserDiaries();
      await api.getUserDiary('2026-10-01');
      expect(calls, [
        '/api/v1/characters/7',
        '/api/v1/memories/11',
        '/api/v1/memories/11/chain',
        '/api/v1/memories/11/history',
        '/api/v1/diary/7',
        '/api/v1/diary/7/dates',
        '/api/v1/moments/1/comments',
        '/api/v1/user/memos',
        '/api/v1/user/diaries',
        '/api/v1/user/diaries/2026-10-01',
      ]);
      expect(seen.length, 10);
    });

    test('CancelToken：真实传到适配器，取消归一为 ApiException(cancelled)', () async {
      final cancelled = Completer<void>();
      final (api, seen) = _rig((o) {
        final c = Completer<ResponseBody>();
        o.cancelToken?.whenCancel.then((_) {
          cancelled.complete();
          if (!c.isCompleted) c.complete(reply({'id': 7, 'name': 'x'}));
        });
        return c.future;
      });
      final token = CancelToken();
      final fut = api.getCharacter(7, cancelToken: token);
      await pumpEventQueue(); // 让请求先抵达适配器
      expect(seen.single.cancelToken, same(token));
      token.cancel('test');
      await cancelled.future;
      await expectLater(fut, throwsApi('cancelled'));
    });

    test('401 → ApiException(unauthorized, statusCode=401)', () async {
      final (api, _) = _rig((o) async => reply({'detail': 'x'}, 401));
      await expectLater(api.listMemos(), throwsApi('unauthorized', 401));
    });

    test('坏封套 → ApiException(unknown)，不得伪装成空列表', () async {
      // 缺 memories 键
      final (a1, _) = _rig((o) async => reply({'total': 0}));
      await expectLater(
          a1.listMemories(),
          throwsA(isA<ApiException>()
              .having((e) => e.kind, 'kind', 'unknown')
              .having((e) => e.message, 'message', contains('malformed'))));
      // 响应根本不是对象
      final (a2, _) = _rig((o) async => reply([1, 2, 3]));
      await expectLater(a2.listMemos(),
          throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')));
      // 条目缺必填字段
      final (a3, _) = _rig((o) async => reply({
            'diaries': [
              {'id': 1}, // 缺 diary_date / content
            ],
            'total': 1,
          }));
      await expectLater(a3.listUserDiaries(),
          throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')));
    });
  });

  group('个人条目 user_id（M1a-3 新增，可空兼容旧响应）', () {
    test('ReaderMemo：user_id 缺失 ⇒ null（降级只读）；存在则保留', () {
      expect(ReaderMemo.fromJson({'id': 1, 'content': 'x'}).userId, isNull);
      expect(ReaderMemo.fromJson({'id': 1, 'content': 'x', 'user_id': 7}).userId, 7);
    });

    test('ReaderUserDiary：同上（不因缺字段而解析失败）', () {
      expect(
          ReaderUserDiary.fromJson(
                  {'id': 1, 'diary_date': '2026-10-16', 'content': 'x'})
              .userId,
          isNull);
      expect(
          ReaderUserDiary.fromJson({
            'id': 1,
            'user_id': 7,
            'diary_date': '2026-10-16',
            'content': 'x',
          }).userId,
          7);
    });

    test('user_id 为字符串 / 浮点也按数值解析（后端兼容）', () {
      expect(ReaderMemo.fromJson({'id': 1, 'content': 'x', 'user_id': '7'}).userId, 7);
      expect(ReaderMemo.fromJson({'id': 1, 'content': 'x', 'user_id': 7.0}).userId, 7);
    });
  });
}
