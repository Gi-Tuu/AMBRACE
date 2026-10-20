// 文本附件（M1a-4）PWA Widget 长期守卫（合成数据，SYNTH_ 前缀）：
// W1 取消/不支持/超限 ⇒ 0 网络、无待发卡；W2 选合法文件只出待发卡、不发请求；
// W3 明确发送 ⇒ POST /file 字段 file + lang 头，成功落 UI 且待发卡消失；
// W4 双击发送 pending 防重复（只 1 次 POST）；W6 发送中切会话取消旧请求、A 的结果不落 B；
// W7 失败 ⇒ 未确认 + 「只 GET 核对」不重发；W8 即时 file_msg 与历史同 id 去重不出现两份；
// W9 退出登录清待发（重登无残留）；W10 历史文件卡（原名/大小/expired 降级，不展开 summary）；
// W11 AttachmentStrings zh/en 成对；W12 进度契约（total 未知 ⇒ 不定进度）。
//
// Harness（FakeAdapter / reply / boot / login）沿用 session_workbench_test.dart 顶部口径；
// 附件用例用**可注入的假选择器**（真实 DOM 选择器另在浏览器探针验证）。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/chat/attachment_state.dart';
import 'package:ambrace_pwa/l10n/attachment_strings.dart';
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

final List<(String, String)> calls = [];
final List<RequestOptions> reqs = [];

FakeAdapter loggingAdapter(Future<ResponseBody> Function(RequestOptions) h) =>
    FakeAdapter((o) {
      calls.add((o.method, o.path));
      reqs.add(o);
      return h(o);
    });

/// 可注入的假选择器：可控返回（取消/拒绝/合法），记录调用次数。
class FakePicker implements TextFilePicker {
  FakePicker(this._handler);
  Future<PickedTextFile?> Function() _handler;
  int calls = 0;
  set handler(Future<PickedTextFile?> Function() h) => _handler = h;
  @override
  Future<PickedTextFile?> pick() {
    calls++;
    return _handler();
  }
}

PickedTextFile pickedFile({String name = 'SYNTH_NOTE.txt', int size = 16}) =>
    PickedTextFile(name: name, size: size, bytes: Uint8List.fromList(List.filled(size, 65)));

Map<String, dynamic> aiMsg(int id, int sid, String content) => {
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

/// 即时上传成功响应（file_message 只有即时字段，不带 size/type/summary）。
Map<String, dynamic> fileUploadReply(int sid, int id, String name,
        {List<Map<String, dynamic>>? chunks}) =>
    {
      'file_message': {
        'id': id,
        'session_id': sid,
        'sender_type': 'user',
        'content': name,
        'file_url': '/uploads/files/$sid/syn.txt',
        'created_at': '2026-10-01T00:00:00',
      },
      'chunks': chunks ?? [aiMsg(id + 1, sid, 'SYNTH_AI_REPLY')],
    };

/// 历史里的文件消息（extra_meta 为 JSON 字符串，含 size/type/summary/expired）。
Map<String, dynamic> fileHistJson(int id, int sid, String name,
        {String size = '1 KB', String type = 'txt', bool expired = false, String summary = ''}) =>
    {
      'id': id,
      'session_id': sid,
      'sender_type': 'user',
      'content': name,
      'created_at': '2026-10-01T00:00:00',
      'extra_meta': jsonEncode({
        'file': {
          'name': name,
          'url': '/uploads/files/$sid/x.$type',
          'size': size,
          'type': type,
          'summary': summary,
          if (expired) 'expired': true,
        },
      }),
    };

bool isForceNew(RequestOptions o) {
  final v = o.queryParameters['force_new'];
  return v == true || v == 'true';
}

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

Future<dynamic> boot(WidgetTester tester, {TextFilePicker? picker}) async {
  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
      MaterialApp(home: app.WorkspacePage(lang: 'zh', picker: picker)));
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

Future<void> intoChat(WidgetTester tester) async {
  await login(tester);
  await tester.tap(find.text('SYNTH_A'));
  await tester.pumpAndSettle();
}

Finder get attachButton => find.byTooltip('选择文本文件');

void main() {
  setUp(() {
    calls.clear();
    reqs.clear();
  });

  testWidgets('W1 取消/不支持/超限：均 0 网络、无待发卡', (tester) async {
    final picker = FakePicker(() async => null);
    await boot(tester, picker: picker);
    await intoChat(tester);

    await tester.tap(attachButton); // 取消：静默
    await tester.pumpAndSettle();
    expect(find.text('发送附件'), findsNothing);

    picker.handler = () async => const PickedTextFile(
        name: 'SYNTH_BAD.exe', size: 3, reject: TextFileRejectReason.unsupportedType);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    expect(find.text('不支持的文件类型（仅 .txt / .md / .csv）'), findsOneWidget);

    picker.handler = () async => const PickedTextFile(
        name: 'SYNTH_BIG.txt', size: 21 * 1024 * 1024, reject: TextFileRejectReason.tooLarge);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    expect(find.text('文件超过 20MB 上限'), findsOneWidget);

    expect(picker.calls, 3);
    expect(calls.where((c) => c.$2.endsWith('/file')), isEmpty, reason: '本地拒绝/取消不得发请求');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W2 选合法文件：只出待发卡，不发请求', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_NOTE.txt'));
    await boot(tester, picker: picker);
    await intoChat(tester);

    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_NOTE.txt'), findsOneWidget);
    expect(find.text('发送附件'), findsOneWidget);
    expect(find.text('待发送'), findsOneWidget);
    expect(calls.where((c) => c.$2.endsWith('/file')), isEmpty, reason: '选择不得偷偷发送');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W3 明确发送：POST /file 字段 file + lang 头，成功落 UI、待发卡消失', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_NOTE.txt'));
    final dynamic state = await boot(tester, picker: picker);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        return reply(fileUploadReply(101, 500, 'SYNTH_NOTE.txt'));
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送附件'));
    await tester.pumpAndSettle();

    expect(calls.where((c) => c.$2 == '/api/v1/chat/sessions/101/file').length, 1);
    final req = reqs.firstWhere((r) => r.path == '/api/v1/chat/sessions/101/file');
    expect(req.method, 'POST');
    expect(req.headers['lang'], 'zh');
    expect(req.data, isA<FormData>());
    final form = req.data as FormData;
    expect(form.files.first.key, 'file');
    expect(form.files.first.value.filename, 'SYNTH_NOTE.txt');
    expect(find.text('发送附件'), findsNothing, reason: '成功后待发卡应消失');
    expect(find.text('SYNTH_NOTE.txt'), findsOneWidget, reason: '文件消息卡可见');
    expect(find.text('SYNTH_AI_REPLY'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W4 双击发送：pending 防重复，只 1 次 POST', (tester) async {
    final picker = FakePicker(() async => pickedFile());
    final dynamic state = await boot(tester, picker: picker);
    final gate = Completer<void>();
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        await gate.future;
        return reply(fileUploadReply(101, 600, 'SYNTH_NOTE.txt'));
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();

    final send = find.text('发送附件');
    await tester.tap(send);
    await tester.tap(send, warnIfMissed: false); // 第二次（未 pump，树未重建）⇒ 被 pending 挡下
    await tester.pump();
    expect(find.text('上传中'), findsOneWidget, reason: '在飞阶段应显示上传中');
    expect(find.text('取消等待'), findsOneWidget);

    gate.complete();
    await tester.pumpAndSettle();
    expect(calls.where((c) => c.$2.endsWith('/file')).length, 1, reason: '只允许 1 次上传请求');
    expect(find.text('发送附件'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W6 发送中切会话：取消旧请求，A 的 file_message 不落 B', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_A_FILE.txt'));
    final dynamic state = await boot(tester, picker: picker);
    final gate = Completer<void>();
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        await gate.future;
        return reply(fileUploadReply(101, 700, 'SYNTH_A_FILE.txt'));
      }
      if (o.path == '/api/v1/chat/sessions/102/messages') {
        return reply({'session_id': 102, 'total': 1, 'messages': [aiMsg(2, 102, 'SYNTH_HIST_102')]});
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送附件'));
    await tester.pump(); // 上传已开始（未完成）
    await tester.tap(find.textContaining('#102')); // 切会话 ⇒ 取消旧附件请求 + 作废
    await tester.pumpAndSettle();

    gate.complete(); // A 的迟到响应
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_HIST_102'), findsOneWidget);
    expect(find.text('SYNTH_A_FILE.txt'), findsNothing, reason: 'A 的文件消息不得写到 B');
    expect(find.text('SYNTH_AI_REPLY'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W7 失败：未确认 + 只 GET 核对、不重发', (tester) async {
    final picker = FakePicker(() async => pickedFile());
    final dynamic state = await boot(tester, picker: picker);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        return reply({'detail': 'SYNTH_ERR'}, 500);
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({'session_id': 101, 'total': 1, 'messages': [aiMsg(1, 101, 'SYNTH_HIST_101')]});
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送附件'));
    await tester.pumpAndSettle();

    expect(find.textContaining('结果未确认'), findsOneWidget);
    expect(find.text('核对历史'), findsOneWidget);

    final before = calls.length;
    await tester.tap(find.text('核对历史'));
    await tester.pumpAndSettle();
    final delta = calls.sublist(before);
    expect(delta.where((c) => c.$1 == 'POST'), isEmpty, reason: '核对只允许 GET，不得重发');
    expect(delta.map((c) => c.$2).toList(), ['/api/v1/chat/sessions/101/messages']);
    expect(find.text('SYNTH_HIST_101'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W8 即时 file_msg 与历史同 id：刷新后不出现两份', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_DEDUP.txt'));
    final dynamic state = await boot(tester, picker: picker);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        return reply(fileUploadReply(101, 800, 'SYNTH_DEDUP.txt'));
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({
          'session_id': 101,
          'total': 2,
          'messages': [fileHistJson(800, 101, 'SYNTH_DEDUP.txt'), aiMsg(801, 101, 'SYNTH_AI_REPLY')],
        });
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送附件'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_DEDUP.txt'), findsOneWidget);

    await tester.tap(find.textContaining('#102'));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('#101')); // 回到 101 ⇒ 历史刷新（同 id）
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_DEDUP.txt'), findsOneWidget, reason: '相同稳定 id 去重，不得两份');
    expect(find.text('SYNTH_AI_REPLY'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W9 退出登录：待发附件一并清（重登无残留）', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_PRIVATE.txt'));
    await boot(tester, picker: picker);
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_PRIVATE.txt'), findsOneWidget);

    await tester.tap(find.text('退出登录'));
    await tester.pumpAndSettle();
    await intoChat(tester); // 重登 + 进聊天
    expect(find.textContaining('SYNTH_PRIVATE.txt'), findsNothing, reason: '退出后待发文件不得残留');
    expect(find.text('发送附件'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('W10 历史文件卡：原名/大小可见；expired 降级；summary 不展开', (tester) async {
    final picker = FakePicker(() async => null);
    final dynamic state = await boot(tester, picker: picker);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({
          'session_id': 101,
          'total': 3,
          'messages': [
            fileHistJson(1, 101, 'SYNTH_DOC.txt', size: '2 KB', type: 'txt', summary: 'SYNTH_SECRET_SUMMARY'),
            fileHistJson(2, 101, 'SYNTH_GONE.txt', size: '3 KB', type: 'txt', expired: true),
            aiMsg(3, 101, 'SYNTH_AI_PLAIN'),
          ],
        });
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    expect(find.text('SYNTH_DOC.txt'), findsOneWidget);
    expect(find.textContaining('2 KB'), findsOneWidget, reason: '可用大小如实展示');
    expect(find.text('SYNTH_GONE.txt'), findsOneWidget);
    expect(find.text('附件已失效或不可用'), findsOneWidget, reason: 'expired 降级为双语状态');
    expect(find.textContaining('SYNTH_SECRET_SUMMARY'), findsNothing, reason: '摘要不得当正文展开');
    expect(find.text('SYNTH_AI_PLAIN'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  test('W11 AttachmentStrings zh/en key 对齐且均有值', () {
    final tables = AttachmentStrings.debugTables;
    final zh = tables['zh']!;
    final en = tables['en']!;
    expect(zh.keys.toSet(), en.keys.toSet());
    for (final k in zh.keys) {
      expect(zh[k]!.isNotEmpty, isTrue, reason: 'zh[$k] 不得为空');
      expect(en[k]!.isNotEmpty, isTrue, reason: 'en[$k] 不得为空');
    }
  });

  test('W12 进度契约：total 未知 ⇒ 不定进度；比例夹在 0..1；阶段 key 正确', () {
    final p = PendingAttachment(
        file: pickedFile(), characterId: 1, sessionId: 101);
    expect(p.fraction, isNull, reason: 'total<=0 ⇒ 不定进度，不伪算');
    expect(p.inFlight, isFalse);
    expect(p.copyWith(loaded: 5, total: 10).fraction, 0.5);
    expect(p.copyWith(loaded: 999, total: 10).fraction, 1.0);
    expect(p.copyWith(loaded: 5, total: 0).fraction, isNull);
    expect(p.copyWith(phase: AttachPhase.uploading).inFlight, isTrue);
    expect(p.copyWith(phase: AttachPhase.waiting).inFlight, isTrue);
    expect(attachPhaseKey(AttachPhase.unconfirmed), 'phaseUnconfirmed');
    expect(humanSize(2048), '2 KB');
  });

  // ── 返工 R2：选择期间 latest-wins ＋ 作废语义 ────────────────────────────────

  testWidgets('R2 选择期间 latest-wins：较早的晚到结果不得覆盖较新选择', (tester) async {
    final first = Completer<PickedTextFile?>();
    final second = Completer<PickedTextFile?>();
    var n = 0;
    final picker = FakePicker(() => ++n == 1 ? first.future : second.future);
    await boot(tester, picker: picker);
    await intoChat(tester);

    await tester.tap(attachButton);
    await tester.pump();
    await tester.tap(attachButton); // 第二次选择（较新）
    await tester.pump();
    expect(picker.calls, 2, reason: '代次法：两次都进入 picker，由代次裁决谁生效');

    second.complete(pickedFile(name: 'SYNTH_NEW_SELECTION.txt'));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_NEW_SELECTION.txt'), findsOneWidget);

    first.complete(pickedFile(name: 'SYNTH_OLD_SELECTION.txt')); // 较早选择的晚到结果
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_NEW_SELECTION.txt'), findsOneWidget,
        reason: '较新选择必须保留');
    expect(find.textContaining('SYNTH_OLD_SELECTION.txt'), findsNothing,
        reason: '较早的晚到结果必须丢弃');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('R2 切会话后晚到的选择结果不得挂回旧文件（且不发请求）', (tester) async {
    final gate = Completer<PickedTextFile?>();
    final picker = FakePicker(() => gate.future);
    await boot(tester, picker: picker);
    await intoChat(tester);

    await tester.tap(attachButton);
    await tester.pump();
    await tester.tap(find.textContaining('#102')); // 切会话 ⇒ 作废在飞选择
    await tester.pumpAndSettle();

    gate.complete(pickedFile(name: 'SYNTH_STALE.txt')); // 作废后的晚到结果
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_STALE.txt'), findsNothing,
        reason: '作废后晚到不得恢复旧文件名/字节');
    expect(calls.where((c) => c.$2.endsWith('/file')), isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('R2 退出登录后晚到的选择结果不得挂回（重登无残留）', (tester) async {
    final gate = Completer<PickedTextFile?>();
    final picker = FakePicker(() => gate.future);
    await boot(tester, picker: picker);
    await intoChat(tester);

    await tester.tap(attachButton);
    await tester.pump();
    await tester.tap(find.text('退出登录')); // 退出 ⇒ 清私文/文件名/字节
    await tester.pumpAndSettle();

    gate.complete(pickedFile(name: 'SYNTH_LOGOUT_STALE.txt'));
    await tester.pumpAndSettle();
    expect(find.textContaining('SYNTH_LOGOUT_STALE.txt'), findsNothing);
    await intoChat(tester); // 重登
    expect(find.textContaining('SYNTH_LOGOUT_STALE.txt'), findsNothing);
    expect(find.text('发送附件'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // ── 返工 R3：上传成功封套缺文件身份 ⇒ 未确认，不消耗待发卡 ────────────────────

  testWidgets('R3 缺文件身份的成功封套：未确认 + 只 GET 核对，不当成功落 UI', (tester) async {
    final picker = FakePicker(() async => pickedFile(name: 'SYNTH_NOMETA.txt'));
    final dynamic state = await boot(tester, picker: picker);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions/101/file') {
        final j = fileUploadReply(101, 900, 'SYNTH_NOMETA.txt');
        (j['file_message'] as Map).remove('file_url'); // 缺实际文件元数据/URL
        return reply(j);
      }
      if (o.path == '/api/v1/chat/sessions/101/messages') {
        return reply({'session_id': 101, 'total': 0, 'messages': []});
      }
      return synthHandler(o);
    });
    await intoChat(tester);
    await tester.tap(attachButton);
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送附件'));
    await tester.pumpAndSettle();

    expect(find.textContaining('结果未确认'), findsOneWidget, reason: '缺文件身份 ⇒ 未确认');
    expect(find.text('核对历史'), findsOneWidget);
    expect(find.textContaining('SYNTH_NOMETA.txt'), findsOneWidget,
        reason: '待发卡必须保留（未被当成功消耗）');
    expect(find.text('SYNTH_AI_REPLY'), findsNothing, reason: '坏封套不得当成功落 AI 回复');

    final before = calls.length;
    await tester.tap(find.text('核对历史'));
    await tester.pumpAndSettle();
    final delta = calls.sublist(before);
    expect(delta.where((c) => c.$1 == 'POST'), isEmpty, reason: '核对只允许 GET，不重传');
    expect(delta.map((c) => c.$2).toList(), ['/api/v1/chat/sessions/101/messages']);
    await tester.pumpWidget(const SizedBox());
  });
}
