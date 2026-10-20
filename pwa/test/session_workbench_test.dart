// 会话工作台（M1a-3）Widget 长期守卫（合成数据，SYNTH_ 前缀）：
// A1 选已存在会话只 GET；A2 同角色多会话 / 换角色草稿不串；
// A3 显式新建只发一次 force_new；A4 新建响应丢失不自动重发 + 刷新列表 + 未确认提示；
// A5 分页原始窗口推进 + id 去重 + 时间正序；A6 会话级慢 A 快 B；
// A7 旧流取消（迟到事件不得写入新会话）；A8 未 ready 禁止发送；
// A9 丢响应**不半切换**（旧会话/草稿/历史/发送归属原样）；A10 丢响应后的核对迟到不覆盖当前选中。
//
// Harness（FakeAdapter / reply / boot 1400×900 / login）照抄 reader_panes_test.dart 顶部。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_pwa/features/chat/session_strip.dart';
import 'package:ambrace_pwa/l10n/session_strings.dart';
import 'package:ambrace_pwa/main.dart' as app;
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
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

ResponseBody reply(Object data, [int status = 200]) => ResponseBody.fromString(
    jsonEncode(data), status,
    headers: {Headers.contentTypeHeader: ['application/json']});

final List<(String, String)> calls = []; // (method, path)
final List<RequestOptions> reqs = []; // 完整请求（用于断言 force_new / query / body）
final List<int> msgSkips = []; // 历史分页请求的 skip 记录

FakeAdapter loggingAdapter(Future<ResponseBody> Function(RequestOptions) h) =>
    FakeAdapter((o) {
      calls.add((o.method, o.path));
      reqs.add(o);
      return h(o);
    });

Map<String, dynamic> msgJson(int id, int sid, String content) => {
      'id': id,
      'session_id': sid,
      'sender_type': 'ai',
      'content': content,
      'created_at': '2026-10-01T00:00:00',
    };

Map<String, dynamic> sessionJson(int id, int cid) => {
      'id': id,
      'character_id': cid,
      'title': null,
      'is_active': true,
      'created_at': '2026-10-01T00:00:00',
      'updated_at': '2026-10-02T00:00:00',
    };

/// force_new 只在显式新建时下发（true）；query 值在不同 dio 版本可能是 bool 或串。
bool isForceNew(RequestOptions o) {
  final v = o.queryParameters['force_new'];
  return v == true || v == 'true';
}

/// 默认处理器：登录 / 本人 id / 角色 / 会话列表（101+102 属角色 1，201 属角色 2）
/// / 历史（任何会话的空页）。各用例按需包一层覆盖。
Future<ResponseBody> synthHandler(RequestOptions o) async {
  if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
  if (o.path == '/api/v1/auth/profile') return reply({'id': 1});
  if (o.path == '/api/v1/characters') {
    return reply({
      'characters': [
        {'id': 1, 'name': 'SYNTH_A'},
        {'id': 2, 'name': 'SYNTH_B'},
      ]
    });
  }
  if (o.path == '/api/v1/chat/sessions') {
    if (o.method == 'GET') {
      final s = [sessionJson(101, 1), sessionJson(102, 1), sessionJson(201, 2)];
      return reply({'sessions': s, 'total': s.length});
    }
    if (isForceNew(o)) return reply({'id': 200});
    final cid = (o.queryParameters['character_id'] as num?)?.toInt() ?? 1;
    return reply({'id': cid == 2 ? 201 : 101});
  }
  if (o.path.endsWith('/messages')) {
    final sid = int.parse(o.path.split('/')[5]);
    return reply({'session_id': sid, 'total': 0, 'messages': []});
  }
  throw StateError('Unexpected endpoint ${o.method} ${o.path}');
}

Future<dynamic> boot(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const app.AmbracePwaApp());
  final dynamic state = tester.state(find.byType(app.WorkspacePage));
  state.env.client.dio.httpClientAdapter = loggingAdapter(synthHandler);
  return state;
}

Future<void> login(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).at(1), 'synthetic_user');
  await tester.enterText(find.byType(TextField).at(2), 'synthetic_password');
  await tester.tap(find.text('登录'));
  await tester.pumpAndSettle();
}

String inputText(WidgetTester tester) =>
    tester.widget<TextField>(find.byType(TextField).last).controller!.text;

void main() {
  setUp(() {
    calls.clear();
    reqs.clear();
    msgSkips.clear();
  });

  // ── A1：选已存在会话只 GET ───────────────────────────────────────────────
  testWidgets('A1 选已存在会话：只 GET 该会话历史，不 POST、不改已读', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({'session_id': 101, 'total': 1, 'messages': [msgJson(1, 101, 'SYNTH_HIST_101')]});
      }
      if (o.path == '/api/v1/chat/sessions/102/messages') {
        return reply({'session_id': 102, 'total': 1, 'messages': [msgJson(2, 102, 'SYNTH_HIST_102')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_HIST_101'), findsOneWidget);

    final before = calls.length;
    await tester.tap(find.textContaining('#102'));
    await tester.pumpAndSettle();
    final delta = calls.sublist(before);
    expect(delta, [('GET', '/api/v1/chat/sessions/102/messages')],
        reason: '选已存在会话只允许一次 GET 历史：$delta');
    expect(delta.where((c) => c.$1 == 'POST'), isEmpty, reason: '不得 POST 建会话或改已读');
    expect(find.text('SYNTH_HIST_102'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  // ── A2：多会话 / 多角色草稿不串 ──────────────────────────────────────────
  testWidgets('A2 同角色多会话草稿不串 + 换角色不串（保留既有承诺）', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_DRAFT_101');

    await tester.tap(find.textContaining('#102'));
    await tester.pumpAndSettle();
    expect(inputText(tester), isEmpty, reason: '同角色换会话不得把 101 草稿带到 102');

    await tester.enterText(find.byType(TextField).last, 'SYNTH_DRAFT_102');
    await tester.tap(find.textContaining('#101'));
    await tester.pumpAndSettle();
    expect(inputText(tester), 'SYNTH_DRAFT_101', reason: '切回 101 草稿必须还在');

    await tester.tap(find.text('SYNTH_B')); // 换角色（另一 role id）
    await tester.pumpAndSettle();
    expect(inputText(tester), isEmpty, reason: '换角色不得把角色 1 的草稿带到角色 2');

    await tester.tap(find.text('SYNTH_A')); // 回到角色 1
    await tester.pumpAndSettle();
    expect(inputText(tester), 'SYNTH_DRAFT_101', reason: '角色往返不得静默丢失未发草稿');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A3：显式新建只发一次 force_new ───────────────────────────────────────
  testWidgets('A3 显式新建只发一次 force_new=true，旧会话仍可选', (tester) async {
    final created = Completer<ResponseBody>();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'POST' && isForceNew(o)) {
        return created.future; // pending：期间再触发
      }
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({
          'sessions': [sessionJson(101, 1), sessionJson(102, 1), sessionJson(200, 1)],
          'total': 3,
        });
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({'session_id': 101, 'total': 1, 'messages': [msgJson(1, 101, 'SYNTH_HIST_101')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_HIST_101'), findsOneWidget);

    // 第一点：进入 pending（按钮随即禁用）
    await tester.tap(find.widgetWithText(OutlinedButton, SessionStrings('zh').t('newSession')));
    await tester.pump();
    // pending 中第二次触发：按钮已禁用，直接走同一回调，检验重入守卫
    tester.widget<SessionStrip>(find.byType(SessionStrip)).onCreate();
    await tester.pump();
    created.complete(reply({'id': 200}));
    await tester.pumpAndSettle();

    final forcePosts = reqs
        .where((r) => r.path == '/api/v1/chat/sessions' && r.method == 'POST' && isForceNew(r))
        .length;
    expect(forcePosts, 1, reason: 'pending 中第二次触发不得再发 force_new 建会话');
    expect(find.textContaining('#101'), findsOneWidget, reason: '新建后旧会话必须仍可选');

    final before = calls.length;
    await tester.tap(find.textContaining('#101'));
    await tester.pumpAndSettle();
    final delta = calls.sublist(before);
    expect(delta, [('GET', '/api/v1/chat/sessions/101/messages')],
        reason: '点旧会话只应 GET 其历史：$delta');
    expect(delta.where((c) => c.$1 == 'POST'), isEmpty);
    expect(find.text('SYNTH_HIST_101'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  // ── A4：新建响应丢失不自动重发 ───────────────────────────────────────────
  testWidgets('A4 新建响应丢失：只 1 次 force_new + 刷新列表 + 未确认提示', (tester) async {
    var forceSent = false;
    var listGets = 0;
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'POST' && isForceNew(o)) {
        forceSent = true;
        return reply({'detail': 'SYNTH_NET_LOST'}, 500); // 响应丢失/失败
      }
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        listGets++;
        final s = forceSent
            ? [sessionJson(101, 1), sessionJson(102, 1), sessionJson(202, 1)]
            : [sessionJson(101, 1), sessionJson(102, 1)];
        return reply({'sessions': s, 'total': s.length});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    final getsBefore = listGets;

    await tester.tap(find.widgetWithText(OutlinedButton, SessionStrings('zh').t('newSession')));
    await tester.pumpAndSettle();

    final forcePosts = reqs
        .where((r) => r.path == '/api/v1/chat/sessions' && r.method == 'POST' && isForceNew(r))
        .length;
    expect(forcePosts, 1, reason: '响应丢失不得自动重发 force_new');
    expect(listGets, greaterThan(getsBefore), reason: '失败后必须重新拉取列表核对');
    expect(find.text(SessionStrings('zh').t('newSessionUnconfirmed')), findsOneWidget,
        reason: '结果未确认必须如实告知');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A5：分页原始窗口推进 + id 去重 + 时间正序 ────────────────────────────
  testWidgets('A5 分页：skip 用原始窗口推进、重复 id 不重渲染、更早的在前', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        msgSkips.add(skip);
        if (skip == 0) {
          return reply({'session_id': 101, 'total': 9, 'messages': [
            msgJson(10, 101, 'SYNTH_M10'),
            msgJson(11, 101, 'SYNTH_M11'),
            msgJson(12, 101, 'SYNTH_M12'),
          ]});
        }
        if (skip == 3) {
          // 窗口与首屏重叠（id 12 重复）⇒ 必须去重，且游标仍按原始条数推进
          return reply({'session_id': 101, 'total': 9, 'messages': [
            msgJson(8, 101, 'SYNTH_M8'),
            msgJson(9, 101, 'SYNTH_M9'),
            msgJson(12, 101, 'SYNTH_M12_DUP'),
          ]});
        }
        return reply({'session_id': 101, 'total': 9, 'messages': [
          msgJson(5, 101, 'SYNTH_M5'),
          msgJson(6, 101, 'SYNTH_M6'),
          msgJson(7, 101, 'SYNTH_M7'),
        ]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    expect(find.text(SessionStrings('zh').t('loadEarlier')), findsOneWidget);
    expect(find.text('SYNTH_M10'), findsOneWidget);

    await tester.tap(find.text(SessionStrings('zh').t('loadEarlier')));
    await tester.pumpAndSettle();
    expect(msgSkips, [0, 3], reason: '第二个请求 skip 必须=首屏原始条数(3)，不是展示条数');
    expect(find.text('SYNTH_M12_DUP'), findsNothing, reason: '重复 id 不得重复渲染新条目');
    expect(find.text('SYNTH_M8'), findsOneWidget);

    await tester.ensureVisible(find.text(SessionStrings('zh').t('loadEarlier')));
    await tester.tap(find.text(SessionStrings('zh').t('loadEarlier')));
    await tester.pumpAndSettle();
    expect(msgSkips, [0, 3, 6],
        reason: '第三次 skip=6=原始窗口(3+3)，不是去重后的展示条数(5)');
    expect(find.text(SessionStrings('zh').t('noEarlier')), findsOneWidget);
    // 更早的在前、整体时间正序
    expect(
        tester.getTopLeft(find.text('SYNTH_M5')).dy,
        lessThan(tester.getTopLeft(find.text('SYNTH_M8')).dy),
        reason: '更早的必须在更上面');
    expect(
        tester.getTopLeft(find.text('SYNTH_M8')).dy,
        lessThan(tester.getTopLeft(find.text('SYNTH_M10')).dy),
        reason: '消息必须时间正序');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A6：会话级慢 A 快 B ──────────────────────────────────────────────────
  testWidgets('A6 会话级慢 A 快 B：A 迟到历史不得覆盖已选的 B', (tester) async {
    final historyA = Completer<ResponseBody>();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') return historyA.future;
      if (o.path == '/api/v1/chat/sessions/102/messages') {
        return reply({'session_id': 102, 'total': 1, 'messages': [msgJson(2, 102, 'SYNTH_B_HISTORY')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle(); // A 历史挂起

    await tester.tap(find.textContaining('#102'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget);

    historyA.complete(reply({'session_id': 101, 'total': 1,
        'messages': [msgJson(1, 101, 'SYNTH_A_HISTORY_LATE')]}));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget, reason: 'B 历史被 A 迟到结果覆盖');
    expect(find.text('SYNTH_A_HISTORY_LATE'), findsNothing, reason: 'A 迟到历史流入新会话');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A7：旧流取消 ─────────────────────────────────────────────────────────
  testWidgets('A7 旧流取消：切会话后旧流迟到事件不得写入新会话', (tester) async {
    final stream = StreamController<Uint8List>();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      if (o.path == '/api/v1/chat/sessions/102/messages') {
        return reply({'session_id': 102, 'total': 1, 'messages': [msgJson(2, 102, 'SYNTH_B_HISTORY')]});
      }
      if (o.path.endsWith('/messages/stream')) {
        return ResponseBody(stream.stream, 200,
            headers: {Headers.contentTypeHeader: ['text/event-stream']});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).last, 'SYNTH_USER_OLD');
    await tester.tap(find.text('发送'));
    await tester.pumpAndSettle(); // 流已挂上（尚未吐事件）
    expect(find.text('SYNTH_USER_OLD'), findsOneWidget); // 本地回显

    // 旧流当前确实活着且会被渲染（证明流通道有效，后面对迟到事件的断言不是空转）
    stream.add(utf8.encode('data: {"type":"delta","text":"SYNTH_LIVE_DELTA"}\n\n'));
    await tester.pump(Duration.zero);
    expect(find.text('SYNTH_LIVE_DELTA'), findsOneWidget, reason: '在飞的流本应可被渲染');

    await tester.tap(find.textContaining('#102')); // 切会话（旧流在飞）
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget);
    expect(find.text('SYNTH_USER_OLD'), findsNothing, reason: '切会话必须清掉旧会话本地回显');
    expect(find.text('SYNTH_LIVE_DELTA'), findsNothing, reason: '切会话必须清掉旧流增量');

    // 旧流此刻才吐事件
    stream.add(utf8.encode('data: {"type":"block","id":999,"content":"SYNTH_OLD_STREAM_LATE"}\n\n'));
    await tester.pump(Duration.zero);
    stream.add(utf8.encode('data: {"type":"done"}\n\n'));
    await tester.pump(Duration.zero);
    stream.close();
    await tester.pumpAndSettle(const Duration(milliseconds: 100),
        EnginePhase.sendSemanticsUpdate, const Duration(seconds: 3));
    expect(find.text('SYNTH_OLD_STREAM_LATE'), findsNothing, reason: '旧流迟到事件写入了新会话');
    expect(find.text('SYNTH_B_HISTORY'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  // ── A8：未 ready 禁止发送 ────────────────────────────────────────────────
  testWidgets('A8 未 ready（会话未建立）禁止发送', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'POST' && !isForceNew(o)) {
        return reply({'detail': 'SYNTH_ERR'}, 500); // 建会话失败 ⇒ _sessionId 保持 null
      }
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).last, 'SYNTH_BLOCKED_SEND');
    await tester.tap(find.text('发送'));
    await tester.pumpAndSettle();
    final streams = calls.where((c) => c.$2.endsWith('/messages/stream')).toList();
    expect(streams, isEmpty, reason: '会话未 ready 不得发送：$streams');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A9：新建丢响应 ⇒ 政策①保持旧会话完整（禁止半切换）────────────────────
  testWidgets('A9 新建丢响应：旧会话/草稿/历史原样、发送仍去旧 sid、只 1 次 force_new', (tester) async {
    var created = false;
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'POST' && isForceNew(o)) {
        created = true;
        return reply({'detail': 'SYNTH_LOST'}, 502); // 结果未确认（响应丢失/失败）
      }
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        final s = [sessionJson(101, 1), if (created) sessionJson(900, 1)];
        return reply({'sessions': s, 'total': s.length});
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({'session_id': 101, 'total': 1,
            'messages': [msgJson(1, 101, 'SYNTH_OLD_HISTORY')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_OLD_DRAFT');

    await tester.tap(
        find.widgetWithText(OutlinedButton, SessionStrings('zh').t('newSession')));
    await tester.pumpAndSettle();

    // 政策①：**不采纳**新会话 ⇒ 选中/草稿/历史原样；绝不半切换
    final strip = tester.widget<SessionStrip>(find.byType(SessionStrip));
    expect(strip.selectedId, 101, reason: '丢响应后不得把发送归属悄悄改到新会话');
    expect(inputText(tester), 'SYNTH_OLD_DRAFT', reason: '旧会话草稿必须原样保留');
    expect(find.text('SYNTH_OLD_HISTORY'), findsOneWidget, reason: '旧会话历史必须仍在');
    expect(find.text(SessionStrings('zh').t('newSessionUnconfirmed')), findsOneWidget,
        reason: '结果未确认必须如实告知');
    final forcePosts = reqs
        .where((r) => r.path == '/api/v1/chat/sessions' && r.method == 'POST' && isForceNew(r))
        .length;
    expect(forcePosts, 1, reason: '丢响应不得自动重发 force_new');

    // 归属仍是 101：发送必须落到 101 的流，不得落到未被采纳的 900
    final before = calls.length;
    await tester.tap(find.widgetWithText(FilledButton, '发送'));
    await tester.pumpAndSettle();
    final delta = calls.sublist(before);
    expect(delta.any((c) => c.$2 == '/api/v1/chat/sessions/101/messages/stream'), isTrue,
        reason: '旧会话仍应可发送：$delta');
    expect(delta.any((c) => c.$2.contains('/sessions/900/')), isFalse,
        reason: '发送不得落到未采纳的新会话：$delta');
    await tester.pumpWidget(const SizedBox());
  });

  // ── A10：丢响应后的核对迟到 ⇒ 不得覆盖用户已切到的会话 ────────────────────
  testWidgets('A10 丢响应后核对迟到：用户已切到 #102，迟到列表不得覆盖当前归属', (tester) async {
    var gets = 0;
    final refresh = Completer<ResponseBody>();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions' && o.method == 'POST' && isForceNew(o)) {
        return reply({'detail': 'SYNTH_LOST'}, 502);
      }
      if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
        gets++;
        if (gets >= 3) return refresh.future; // 丢响应后的「核对」列表：挂起
        return reply({'sessions': [sessionJson(101, 1), sessionJson(102, 1)], 'total': 2});
      }
      if (o.path == '/api/v1/chat/sessions/102/messages') {
        return reply({'session_id': 102, 'total': 1,
            'messages': [msgJson(2, 102, 'SYNTH_HIST_102')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();

    await tester.tap(
        find.widgetWithText(OutlinedButton, SessionStrings('zh').t('newSession')));
    await tester.pump(); // 新建失败 + 核对列表挂起

    await tester.tap(find.textContaining('#102')); // 用户改选 102（代次作废旧核对）
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_HIST_102'), findsOneWidget);

    refresh.complete(reply({
      'sessions': [sessionJson(101, 1), sessionJson(102, 1), sessionJson(900, 1)],
      'total': 3,
    }));
    await tester.pumpAndSettle();
    final strip = tester.widget<SessionStrip>(find.byType(SessionStrip));
    expect(strip.selectedId, 102, reason: '迟到的核对列表不得覆盖用户已选的 102');
    expect(find.text('SYNTH_HIST_102'), findsOneWidget, reason: '迟到核对不得换掉当前历史');
    await tester.pumpWidget(const SizedBox());
  });
}
