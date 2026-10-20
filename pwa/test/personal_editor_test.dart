// 个人内容编辑（M1a-3）Widget 长期守卫（合成数据，SYNTH_ 前缀）：
// B9 身份未就绪 ⇒ 无写入口 / 新建禁用；B10 旧响应缺 user_id ⇒ 降级只读；
// B11 编辑保存失败 ⇒ 表单不关、原文保留；B12 删除取消 0 写；
// B13 删除成功才移除（备忘按 id、日记按数值 id）；B14 家人同日日记不得写错本人；
// B15 打开编辑器后 401 立即清私文；B16 换号 B 正对照 + 无 A 残留 + A 草稿不复活；
// B17 系统返回 dirty 二选一（干净直接关 / 放弃 / 继续编辑，只一个确认框）；
// B18/B19 保存中字段禁用 + 双击只发一次 + 失败恢复可编辑·成功才关（备忘 / 日记）；
// B21 保存中 401 强制作废私文 route，晚到保存结果不得关新 route 或弹旧提示。
//
// Harness（FakeAdapter / reply / boot 1400×900 / login）照抄 reader_panes_test.dart 顶部。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_pwa/features/editor/personal_editor_sheet.dart';
import 'package:ambrace_pwa/l10n/editor_strings.dart';
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
final List<RequestOptions> reqs = []; // 完整请求（用于断言 DELETE 路径 / POST body）

FakeAdapter loggingAdapter(Future<ResponseBody> Function(RequestOptions) h) =>
    FakeAdapter((o) {
      calls.add((o.method, o.path));
      reqs.add(o);
      return h(o);
    });

String todayLocal() {
  final n = DateTime.now();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${n.year.toString().padLeft(4, '0')}-${two(n.month)}-${two(n.day)}';
}

/// 备忘合成行；[userId] 为空 ⇒ **不带** user_id 字段（模拟旧响应缺失）。
Map<String, dynamic> memoJson(int id, int? userId, String content, {String? title}) {
  final m = <String, dynamic>{
    'id': id,
    'title': title,
    'content': content,
    'created_at': '2026-10-01T00:00:00',
    'updated_at': '2026-10-01T00:00:00',
  };
  if (userId != null) m['user_id'] = userId;
  return m;
}

/// 个人日记合成行；[userId] 为空 ⇒ **不带** user_id 字段。
Map<String, dynamic> diaryJson(int id, int? userId, String date, String content) {
  final m = <String, dynamic>{
    'id': id,
    'diary_date': date,
    'content': content,
    'created_at': '2026-10-01T00:00:00',
    'updated_at': '2026-10-01T00:00:00',
  };
  if (userId != null) m['user_id'] = userId;
  return m;
}

/// 默认处理器：登录 / 本人 id=1 / 角色 / 会话（空）/ 历史（空）/ 备忘·日记（空列表）。
/// 各用例按需包一层覆盖。
Future<ResponseBody> base(RequestOptions o) async {
  if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
  if (o.path == '/api/v1/auth/profile') return reply({'id': 1});
  if (o.path == '/api/v1/characters') {
    return reply({'characters': [{'id': 1, 'name': 'SYNTH_A'}]});
  }
  if (o.path == '/api/v1/chat/sessions') {
    if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
    return reply({'id': 101});
  }
  if (o.path.endsWith('/messages')) {
    return reply({'session_id': 101, 'total': 0, 'messages': []});
  }
  if (o.path == '/api/v1/user/memos') return reply({'memos': [], 'total': 0});
  if (o.path == '/api/v1/user/diaries') return reply({'diaries': [], 'total': 0});
  throw StateError('Unexpected endpoint ${o.method} ${o.path}');
}

Future<dynamic> boot(WidgetTester tester) async {
  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const app.AmbracePwaApp());
  final dynamic state = tester.state(find.byType(app.WorkspacePage));
  state.env.client.dio.httpClientAdapter = loggingAdapter(base);
  return state;
}

Future<void> login(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).at(1), 'synthetic_user');
  await tester.enterText(find.byType(TextField).at(2), 'synthetic_password');
  await tester.tap(find.text('登录'));
  await tester.pumpAndSettle();
}

/// 编辑表单内的第 [index] 个输入框（备忘：0=标题 1=正文；日记：0=日期 1=正文）。
Finder sheetField(int index) => find
    .descendant(of: find.byType(PersonalEditorSheet), matching: find.byType(TextField))
    .at(index);

String inputText(WidgetTester tester) =>
    tester.widget<TextField>(find.byType(TextField).last).controller!.text;

void main() {
  setUp(() {
    calls.clear();
    reqs.clear();
  });

  // ── B9：身份未就绪 ⇒ 无写入口 ────────────────────────────────────────────
  testWidgets('B9 身份未就绪：备忘/日记无写入口，新建按钮禁用', (tester) async {
    final today = todayLocal();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/auth/profile') return reply({'detail': 'SYNTH_ERR'}, 500);
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_MINE')]});
      }
      if (o.path == '/api/v1/user/diaries') {
        return reply({'diaries': [diaryJson(2, 1, today, 'SYNTH_MYDIARY_MINE')]});
      }
      return base(o);
    });
    await login(tester);

    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text(EditorStrings('zh').t('identityNotReady')), findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newMemo')))
            .enabled,
        isFalse,
        reason: '身份未就绪新建备忘必须禁用');
    expect(find.byIcon(Icons.edit_outlined), findsNothing);
    expect(find.byIcon(Icons.delete_outline), findsNothing);

    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    expect(find.text(EditorStrings('zh').t('identityNotReady')), findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newDiary')))
            .enabled,
        isFalse,
        reason: '身份未就绪新建日记必须禁用');
    expect(find.byIcon(Icons.edit_outlined), findsNothing);
    expect(find.byIcon(Icons.delete_outline), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // ── B10：旧响应缺 user_id ⇒ 降级只读 ─────────────────────────────────────
  testWidgets('B10 旧响应缺 user_id：条目只读（无编辑/删除入口）', (tester) async {
    final today = todayLocal();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, null, 'SYNTH_MEMO_LEGACY')]});
      }
      if (o.path == '/api/v1/user/diaries') {
        return reply({'diaries': [diaryJson(2, null, today, 'SYNTH_MYDIARY_LEGACY')]});
      }
      return base(o);
    });
    await login(tester);

    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_LEGACY'), findsOneWidget);
    // 身份已就绪（profile id=1）⇒ 新建可用，但条目缺 user_id ⇒ 不得猜本人、只读
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newMemo')))
            .enabled,
        isTrue);
    expect(find.byIcon(Icons.edit_outlined), findsNothing);
    expect(find.byIcon(Icons.delete_outline), findsNothing);

    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MYDIARY_LEGACY'), findsOneWidget);
    expect(find.byIcon(Icons.edit_outlined), findsNothing);
    expect(find.byIcon(Icons.delete_outline), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // ── B11：编辑保存失败保留原文 ────────────────────────────────────────────
  testWidgets('B11 编辑保存失败：表单不关、正文字段保留、错误文案出现', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_ORIG')]});
      }
      if (o.path == '/api/v1/user/memos/1' && o.method == 'PUT') {
        return reply({'detail': 'SYNTH_ERR'}, 500);
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.edit_outlined));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsOneWidget);

    await tester.enterText(sheetField(1), 'SYNTH_MEMO_EDITED');
    await tester.tap(find.text(EditorStrings('zh').t('save')));
    await tester.pumpAndSettle();

    expect(find.byType(PersonalEditorSheet), findsOneWidget, reason: '保存失败不得关闭表单');
    expect(tester.widget<TextField>(sheetField(1)).controller!.text, 'SYNTH_MEMO_EDITED',
        reason: '失败必须保留正文原文');
    expect(find.textContaining(EditorStrings('zh').t('saveFailed')), findsOneWidget,
        reason: '必须出现本地化错误文案');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B12：删除取消 0 写 ───────────────────────────────────────────────────
  testWidgets('B12 删除取消：不得发出任何 DELETE', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_BODY')]});
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();

    final before = calls.length;
    await tester.tap(find.byIcon(Icons.delete_outline));
    await tester.pumpAndSettle();
    await tester.tap(find.text(EditorStrings('zh').t('cancel')));
    await tester.pumpAndSettle();

    final delta = calls.sublist(before);
    expect(delta.where((c) => c.$1 == 'DELETE'), isEmpty, reason: '取消不得发 DELETE：$delta');
    expect(find.text('SYNTH_MEMO_BODY'), findsOneWidget, reason: '取消后条目必须仍在');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B13：删除成功才移除 ──────────────────────────────────────────────────
  testWidgets('B13 删除成功才移除：备忘 DELETE /memos/{id}，日记 DELETE /diaries/{数值id}',
      (tester) async {
    final today = todayLocal();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos' && o.method == 'GET') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_BODY')]});
      }
      if (o.path == '/api/v1/user/memos/1' && o.method == 'DELETE') {
        return reply({'ok': true});
      }
      if (o.path == '/api/v1/user/diaries' && o.method == 'GET') {
        return reply({'diaries': [diaryJson(5, 1, today, 'SYNTH_MYDIARY_BODY')]});
      }
      if (o.path == '/api/v1/user/diaries/5' && o.method == 'DELETE') {
        return reply({'ok': true});
      }
      return base(o);
    });
    await login(tester);

    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.delete_outline));
    await tester.pumpAndSettle();
    await tester.tap(find.text(EditorStrings('zh').t('delete')));
    await tester.pumpAndSettle();
    expect(calls.contains(('DELETE', '/api/v1/user/memos/1')), isTrue,
        reason: '删除备忘必须按数值 id DELETE');
    expect(find.text('SYNTH_MEMO_BODY'), findsNothing, reason: '删除成功才移除');

    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.delete_outline));
    await tester.pumpAndSettle();
    await tester.tap(find.text(EditorStrings('zh').t('delete')));
    await tester.pumpAndSettle();
    expect(calls.contains(('DELETE', '/api/v1/user/diaries/5')), isTrue,
        reason: '删除日记必须按数值 id（不是日期）');
    expect(find.text('SYNTH_MYDIARY_BODY'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // ── B14：家人同日日记不得写错本人 ────────────────────────────────────────
  testWidgets('B14 家人同日日记：本人新建初值不取家人正文，提交命中本人日期', (tester) async {
    final today = todayLocal();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/diaries' && o.method == 'GET') {
        return reply({'diaries': [diaryJson(9, 2, today, 'SYNTH_FAMILY_TODAY')]});
      }
      if (o.path == '/api/v1/user/diaries' && o.method == 'POST') {
        final d = (o.data as Map)['diary_date'];
        return reply({
          'id': 7,
          'user_id': 1,
          'diary_date': d,
          'content': 'SYNTH_MY_NEW',
          'created_at': '2026-10-17T00:00:00',
          'updated_at': '2026-10-17T00:00:00',
        });
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_FAMILY_TODAY'), findsOneWidget, reason: '家人条目在共享列表中');

    await tester.tap(find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newDiary')));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(sheetField(1)).controller!.text, isEmpty,
        reason: '新建日记初值不得取家人的同日正文（本人当天无条目 ⇒ 空）');

    await tester.enterText(sheetField(1), 'SYNTH_MY_NEW');
    await tester.tap(find.text(EditorStrings('zh').t('save')));
    await tester.pumpAndSettle();
    final post = reqs.lastWhere((r) => r.path == '/api/v1/user/diaries' && r.method == 'POST');
    expect((post.data as Map)['diary_date'], today, reason: '必须命中本人日期 upsert');
    expect((post.data as Map)['content'], 'SYNTH_MY_NEW');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B15：401 打开编辑器立即清私文 ────────────────────────────────────────
  testWidgets('B15 打开编辑器后 401：编辑器 route 作废、回登录屏、旧私文不可见', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_ORIG')]});
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.edit_outlined));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsOneWidget);
    await tester.enterText(sheetField(1), 'SYNTH_PRIVATE_EDIT');

    state.env.client.onUnauthorized!();
    await tester.pumpAndSettle();
    expect(find.byType(BottomSheet), findsNothing, reason: '401 必须作废编辑 route');
    expect(find.text('登录'), findsOneWidget, reason: '401 必须真清到登录屏');
    expect(find.text('SYNTH_PRIVATE_EDIT'), findsNothing, reason: '旧私文不得留在可返回位置');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B16：换号正对照 + 无 A 残留 ──────────────────────────────────────────
  testWidgets('B16 换号：B 正对照、无 A 残留、A 未发草稿不复活', (tester) async {
    var account = 'A';
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/auth/profile') {
        return reply({'id': account == 'A' ? 1 : 2});
      }
      if (o.path == '/api/v1/user/memos') {
        return reply({
          'memos': [memoJson(1, account == 'A' ? 1 : 2, 'SYNTH_MEMO_$account')]
        });
      }
      return base(o);
    });
    await login(tester);

    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_A'), findsOneWidget);

    // A 在 chat 留下未发草稿
    await tester.tap(find.text('聊天'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_DRAFT_A');

    await tester.tap(find.text('退出登录'));
    await tester.pumpAndSettle();

    account = 'B';
    await login(tester);
    await tester.tap(find.text('SYNTH_A')); // 换号后重开同一角色
    await tester.pumpAndSettle();
    expect(inputText(tester), isEmpty, reason: 'A 的未发草稿不得复活');

    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_B'), findsOneWidget, reason: 'B 账号正对照');
    expect(find.text('SYNTH_MEMO_A'), findsNothing, reason: '换号后旧账号数据残留');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B17：系统返回与 dirty 生命周期（§4）──────────────────────────────────
  testWidgets('B17 系统返回：干净直接关；dirty 只给一个确认框，继续保留原文、放弃才关', (tester) async {
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_ORIG')]});
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();

    // 干净表单：系统返回直接关（不做多余拦阻）
    await tester.tap(find.byIcon(Icons.edit_outlined));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsOneWidget);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsNothing, reason: '干净表单系统返回应直接关');

    // dirty 表单：系统返回必须给决策，不得静默丢弃
    await tester.tap(find.byIcon(Icons.edit_outlined));
    await tester.pumpAndSettle();
    await tester.enterText(sheetField(1), 'SYNTH_MEMO_DIRTY');
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsOneWidget, reason: 'dirty 不得被返回静默丢弃');
    expect(find.byType(AlertDialog), findsOneWidget, reason: '每次只允许一个确认框');
    expect(find.text(EditorStrings('zh').t('discardTitle')), findsOneWidget);

    // 继续编辑 ⇒ 表单仍在、原文保留
    await tester.tap(find.text(EditorStrings('zh').t('keepEditing')));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
    expect(find.byType(PersonalEditorSheet), findsOneWidget);
    expect(tester.widget<TextField>(sheetField(1)).controller!.text, 'SYNTH_MEMO_DIRTY',
        reason: '继续编辑必须原样保留未保存内容');

    // 再返回 → 放弃 ⇒ 才关
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    await tester.tap(find.text(EditorStrings('zh').t('discard')));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsNothing, reason: '只有放弃才关');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B18：保存中字段只读 + 失败恢复 + 双击只发一次（备忘）──────────────────
  testWidgets('B18 备忘保存中：字段禁用、双击只发一次；失败后恢复可编辑且原文保留', (tester) async {
    final done = Completer<ResponseBody>();
    var posts = 0;
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos' && o.method == 'GET') {
        return reply({'memos': []});
      }
      if (o.path == '/api/v1/user/memos' && o.method == 'POST') {
        posts++;
        return done.future;
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newMemo')));
    await tester.pumpAndSettle();
    await tester.enterText(sheetField(1), 'SYNTH_MEMO_SUBMITTED');
    await tester.tap(find.text(EditorStrings('zh').t('save')));
    await tester.pumpAndSettle(); // 让写请求真正派发（请求挂起，表单无动画）

    expect(posts, 1, reason: '首次保存必须只发出一次写请求');
    expect(tester.widget<TextField>(sheetField(0)).enabled, isFalse,
        reason: '保存中标题必须禁用（不接受提交快照后的新输入）');
    expect(tester.widget<TextField>(sheetField(1)).enabled, isFalse, reason: '保存中正文必须禁用');
    tester.widget<FilledButton>(find.byType(FilledButton)).onPressed?.call(); // 二次触发
    await tester.pumpAndSettle();
    expect(posts, 1, reason: '保存中不得重复提交');

    done.complete(reply({'detail': 'SYNTH_ERR'}, 500));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsOneWidget, reason: '失败不得关闭表单');
    expect(tester.widget<TextField>(sheetField(1)).enabled, isTrue,
        reason: '失败后必须恢复可编辑');
    expect(tester.widget<TextField>(sheetField(1)).controller!.text, 'SYNTH_MEMO_SUBMITTED',
        reason: '失败必须保留原文便于重试');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B19：保存中字段只读 + 成功才关 + 双击只发一次（个人日记）──────────────
  testWidgets('B19 日记保存中：字段禁用、双击只发一次、成功才关', (tester) async {
    final today = todayLocal();
    final done = Completer<ResponseBody>();
    var posts = 0;
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/diaries' && o.method == 'GET') {
        return reply({'diaries': []});
      }
      if (o.path == '/api/v1/user/diaries' && o.method == 'POST') {
        posts++;
        return done.future;
      }
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newDiary')));
    await tester.pumpAndSettle();
    await tester.enterText(sheetField(0), today);
    await tester.enterText(sheetField(1), 'SYNTH_DIARY_NEW');
    await tester.tap(find.text(EditorStrings('zh').t('save')));
    await tester.pumpAndSettle(); // 让写请求真正派发（请求挂起）

    expect(posts, 1, reason: '首次保存必须只发出一次写请求');
    expect(tester.widget<TextField>(sheetField(0)).enabled, isFalse, reason: '保存中日期必须禁用');
    expect(tester.widget<TextField>(sheetField(1)).enabled, isFalse, reason: '保存中正文必须禁用');
    tester.widget<FilledButton>(find.byType(FilledButton)).onPressed?.call();
    await tester.pumpAndSettle();
    expect(posts, 1, reason: '保存中不得重复提交');

    done.complete(reply(diaryJson(7, 1, today, 'SYNTH_DIARY_NEW')));
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsNothing, reason: '成功才关');
    await tester.pumpWidget(const SizedBox());
  });

  // ── B21：保存中 401 强制清 + 晚到结果不得污染新 route ─────────────────────
  testWidgets('B21 保存中 401：私文 route 立即作废；晚到保存结果不得关新 route/弹旧提示', (tester) async {
    final done = Completer<ResponseBody>();
    final dynamic state = await boot(tester);
    state.env.client.dio.httpClientAdapter = loggingAdapter((o) async {
      if (o.path == '/api/v1/user/memos' && o.method == 'GET') {
        return reply({'memos': [memoJson(1, 1, 'SYNTH_MEMO_ORIG')]});
      }
      if (o.path == '/api/v1/user/memos' && o.method == 'POST') return done.future;
      return base(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, EditorStrings('zh').t('newMemo')));
    await tester.pumpAndSettle();
    await tester.enterText(sheetField(1), 'SYNTH_PRIVATE_SAVE');
    await tester.tap(find.text(EditorStrings('zh').t('save')));
    await tester.pump();

    state.env.client.onUnauthorized!(); // 保存进行中 401
    await tester.pumpAndSettle();
    expect(find.byType(PersonalEditorSheet), findsNothing, reason: '401 必须强制作废编辑 route');
    expect(find.byType(AlertDialog), findsNothing, reason: '401 强制清不得被 dirty 确认拦住');
    expect(find.text('登录'), findsOneWidget, reason: '401 必须真清到登录屏');
    expect(find.text('SYNTH_MEMO_ORIG'), findsNothing, reason: '旧私文不得留在可返回位置');

    done.complete(reply(memoJson(2, 1, 'SYNTH_PRIVATE_SAVE'))); // 晚到结果
    await tester.pumpAndSettle();
    expect(find.text('登录'), findsOneWidget, reason: '晚到结果不得关掉登录屏（新 route）');
    expect(find.byType(PersonalEditorSheet), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
