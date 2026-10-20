// 只读工作台（M1a-2）六模块 Widget 守卫：列表→详情渲染、五态、续页/筛选、
// 慢A快B竞态、换号清理、零业务写、双语成对、日期语义。全部为合成数据（SYNTH_ 前缀）。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_pwa/features/reader/moments_pane.dart';
import 'package:ambrace_pwa/l10n/reader_strings.dart';
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

/// 记忆列表请求的 query 记录：用于断言 `read_only` 与续页游标语义（返工 §2C/§2D/§2E）。
final List<Map<String, dynamic>> memQueryLog = [];

Map<String, dynamic> memJson(int id, int cid, String type, String content) => {
      'id': id, 'character_id': cid, 'memory_type': type,
      'content': content, 'importance': 3, 'importance_pct': 60,
      'is_pinned': false, 'is_locked': false, 'is_archived': false,
      'source_label': '聊天', 'epistemic_status': 'INFERRED',
      'created_at': '2026-10-01T01:00:00', 'updated_at': '2026-10-01T01:00:00',
    };

/// 动态条目合成应答（游标/空页/重复页用例）。
Map<String, dynamic> momentJson(int id, String content) => {
      'id': id, 'character_id': 1, 'character_name': 'SYNTH_A', 'user_id': 0,
      'sender_type': 'ai', 'content': content,
      'image_url': null, 'image_desc': null, 'likes_count': 0, 'is_active': true,
      'created_at': '2026-10-01T00:30:00', 'author_tz_offset': 8,
      'liked_by_me': false, 'likers': [], 'comments': [],
    };

/// 六模块合成应答（memorySlow 非空时角色 1 的记忆列表挂起）。
Future<ResponseBody> synthHandler(RequestOptions o, {Completer<ResponseBody>? memorySlow}) async {
  calls.add((o.method, o.path));
  if (o.path.endsWith('/login')) return reply({'access_token': 'SYNTH_TOKEN'});
  if (o.path == '/api/v1/auth/profile') return reply({'id': 1});
  if (o.path == '/api/v1/chat/sessions' && o.method == 'GET') {
    return reply({'sessions': [], 'total': 0}); // 工作台会话列表（本组用例不涉会话）
  }
  if (o.path == '/api/v1/characters') {
    return reply({'characters': [{'id': 1, 'name': 'SYNTH_A'}, {'id': 2, 'name': 'SYNTH_B'}]});
  }
  if (o.path.endsWith('/characters/1') || o.path.endsWith('/characters/2')) {
    return reply({'id': 1, 'name': 'SYNTH_A', 'bio': 'SYNTH_BIO',
        'system_prompt': 'SYNTH_SECRET_PROMPT', 'created_at': '2026-01-01T00:00:00'});
  }
  if (o.path == '/api/v1/memories') {
    memQueryLog.add(Map<String, dynamic>.from(o.queryParameters));
    if (memorySlow != null && o.queryParameters['character_id'] == 1) {
      return memorySlow.future;
    }
    final cid = o.queryParameters['character_id'];
    final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
    if (skip == 0) {
      return reply({'total': 52, 'memories': [
        for (var i = 0; i < 50; i++)
          memJson(i + 1, cid, i.isEven ? 'event' : 'preference', 'SYNTH_MEM_$i'),
      ]});
    }
    return reply({'total': 52, 'memories': [
      memJson(50, cid, 'event', 'SYNTH_MEM_DUP'),
      memJson(51, cid, 'event', 'SYNTH_MEM_P2'),
    ]});
  }
  if (o.path.endsWith('/chain')) {
    return reply({'status': 'ok', 'chain': [
      {'id': 1, 'content': 'SYNTH_CHAIN_NODE', 'memory_type': 'event',
       'created_at': '2026-10-01T01:00:00', 'is_current': true, 'is_archived': false},
    ]});
  }
  if (o.path.endsWith('/history')) {
    return reply({'status': 'ok', 'memory_id': 1,
        'fact': {'content': 'SYNTH_FACT_NOW', 'status': 'active', 'epistemic_status': 'FACT'},
        'corrections': [{'seq': 1, 'old_id': 1, 'new_id': 2, 'old_value': 'SYNTH_OLD',
          'new_value': 'SYNTH_NEW', 'changed_at': '2026-10-02T00:00:00',
          'changed_at_from': null, 'old_source': null, 'new_source': null,
          'reason': 'SYNTH_REASON', 'actor': null}],
        'truncated': false});
  }
  if (o.path.contains('/diary/') && o.path.endsWith('/dates')) {
    return reply({'dates': ['2028-02-29']});
  }
  if (o.path.startsWith('/api/v1/diary/')) {
    return reply({'total': 1, 'entries': [
      {'id': 1, 'character_id': 1, 'diary_date': '2028-02-29',
       'content': 'SYNTH_DIARY_BODY', 'created_at': '2028-02-29T12:00:00'},
    ]});
  }
  if (o.path.endsWith('/comments')) {
    return reply({'total': 1, 'comments': [
      {'id': 1, 'moment_id': 9, 'parent_id': null, 'sender_type': 'user',
       'sender_id': 1, 'sender_name': 'SYNTH_COMMENTER',
       'content': 'SYNTH_COMMENT_LIVE', 'created_at': '2026-10-02T00:00:00',
       'replies': []},
    ]});
  }
  if (o.path == '/api/v1/moments') {
    return reply({'total': 1, 'moments': [
      {'id': 9, 'character_id': 1, 'character_name': 'SYNTH_A', 'user_id': 0,
       'sender_type': 'ai', 'content': 'SYNTH_MOMENT_BODY',
       'image_url': 'http://invalid.invalid/SYNTH_IMG.png',
       'image_desc': 'SYNTH_IMG_DESC', 'likes_count': 2, 'is_active': true,
       'created_at': '2026-10-01T00:30:00', 'author_tz_offset': 8,
       'liked_by_me': false, 'likers': ['SYNTH_LIKER'], 'comments': []},
    ]});
  }
  if (o.path == '/api/v1/user/memos') {
    return reply({'total': 1, 'memos': [
      {'id': 1, 'title': null, 'content': 'SYNTH_MEMO_BODY',
       'created_at': '2026-10-01T00:00:00', 'updated_at': '2026-10-01T00:00:00'},
    ]});
  }
  if (o.path.startsWith('/api/v1/user/diaries/')) {
    return reply({'id': 1, 'diary_date': '2026-10-05', 'content': 'SYNTH_MYDIARY_LIVE',
        'created_at': '2026-10-05T00:00:00', 'updated_at': '2026-10-05T00:00:00'});
  }
  if (o.path == '/api/v1/user/diaries') {
    return reply({'total': 1, 'diaries': [
      {'id': 1, 'diary_date': '2026-10-05', 'content': 'SYNTH_MYDIARY_BODY',
       'created_at': '2026-10-05T00:00:00', 'updated_at': '2026-10-05T00:00:00'},
    ]});
  }
  throw StateError('Unexpected endpoint ${o.method} ${o.path}');
}

Future<dynamic> boot(WidgetTester tester, {Completer<ResponseBody>? memorySlow}) async {
  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const app.AmbracePwaApp());
  final dynamic state = tester.state(find.byType(app.WorkspacePage));
  state.env.client.dio.httpClientAdapter =
      FakeAdapter((o) => synthHandler(o, memorySlow: memorySlow));
  return state;
}

Future<void> login(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).at(1), 'synthetic_user');
  await tester.enterText(find.byType(TextField).at(2), 'synthetic_password');
  await tester.tap(find.text('登录'));
  await tester.pumpAndSettle();
}

Future<void> pickCharA(WidgetTester tester) async {
  await tester.tap(find.text('SYNTH_A'));
  await tester.pumpAndSettle();
}

Future<void> closeSheet(WidgetTester tester) async {
  await tester.tapAt(const Offset(700, 20)); // 点抽屉外遮罩关闭
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    calls.clear();
    memQueryLog.clear();
  });

  testWidgets('角色详情：白名单字段渲染，内部提示词不展示', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    expect(find.text('SYNTH_BIO'), findsOneWidget);
    expect(find.text('SYNTH_SECRET_PROMPT'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('记忆：列表→详情（链条+事实历史）+ 续页去重 + 类型筛选', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    expect(find.text('SYNTH_MEM_0'), findsOneWidget);
    // 续页：skip 50，按 id 去重（50 重复不出现第二份）
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEM_P2'), findsOneWidget);
    expect(find.text('SYNTH_MEM_DUP'), findsNothing, reason: '续页重复 id 未去重');
    expect(calls.where((c) => c.$2 == '/api/v1/memories').length, 2);
    // 详情抽屉：正文 + 链条 + 事实历史（各自独立加载）
    await tester.tap(find.text('SYNTH_MEM_P2'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_CHAIN_NODE'), findsOneWidget);
    expect(find.text('SYNTH_FACT_NOW'), findsOneWidget);
    expect(find.text('SYNTH_OLD → SYNTH_NEW'), findsOneWidget);
    expect(find.textContaining('INFERRED'), findsWidgets); // 认知状态原文展示
    await closeSheet(tester);
    // 类型筛选（选项来自已加载数据）
    await tester.tap(find.byType(DropdownButton<String?>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('event').last);
    await tester.pumpAndSettle();
    expect(calls.last.$2, '/api/v1/memories');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('角色日记：闰日原样 + 跨年月导航（零时区换算）', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('角色日记'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    // 导航到 2028-02（闰月）：纯年月算术
    final now = DateTime.now();
    var delta = (2028 * 12 + 1) - (now.year * 12 + now.month - 1);
    if (delta <= 0) delta += 12;
    final label = delta % 12 == 1 ? '${2028 + delta ~/ 12}-02' : '2028-02';
    for (var i = 0; i < delta; i++) {
      await tester.tap(find.byIcon(Icons.chevron_right));
      await tester.pumpAndSettle();
    }
    expect(find.text(label), findsWidgets);
    expect(find.text('2028-02-29'), findsWidgets); // 闰日原样，不移一天
    await tester.tap(find.text('2028-02-29').first);
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_DIARY_BODY'), findsWidgets);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('动态：作者时区格式化 + 图片占位 + 详情实时评论树', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MOMENT_BODY'), findsOneWidget);
    expect(find.textContaining('2026-10-01 08:30'), findsOneWidget); // UTC+8
    expect(find.textContaining('SYNTH_IMG_DESC'), findsOneWidget); // 描述占位
    await tester.tap(find.text('SYNTH_MOMENT_BODY'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_COMMENT_LIVE'), findsOneWidget); // 来自 /comments 实时拉取
    // 不加载图片：适配器若收到 image_url 请求会抛 Unexpected endpoint（上面已过）
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('个人备忘：空标题占位 + 详情', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('（无标题）'), findsWidgets);
    await tester.tap(find.text('SYNTH_MEMO_BODY'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_BODY'), findsWidgets);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('个人日记：按月过滤 + 单日详情实时拉取', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    // 导航到 2026-10（合成数据所在月）
    final now = DateTime.now();
    var delta = (now.year * 12 + now.month - 1) - (2026 * 12 + 9);
    for (var i = 0; i < delta; i++) {
      await tester.tap(find.byIcon(Icons.chevron_left));
      await tester.pumpAndSettle();
    }
    expect(find.text('2026-10-05'), findsOneWidget);
    await tester.tap(find.text('2026-10-05'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MYDIARY_LIVE'), findsOneWidget); // GET /user/diaries/{date}
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('五态：服务错误显示重试，重试后恢复', (tester) async {
    var fail = true;
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/user/memos' && fail) {
        return reply({'detail': 'SYNTH_ERR'}, 500);
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('暂无内容'), findsNothing); // 失败不是空列表
    fail = false;
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_BODY'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('竞态：记忆 A 慢 B 快——A 迟到结果不得覆盖 B', (tester) async {
    final slow = Completer<ResponseBody>();
    await boot(tester, memorySlow: slow);
    await login(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A'));
    await tester.pump(); // A 挂起中
    await tester.tap(find.text('SYNTH_B'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEM_0'), findsOneWidget); // B 的列表已显示
    slow.complete(reply({'total': 1, 'memories': [
      memJson(999, 1, 'event', 'SYNTH_LATE_A'),
    ]}));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_LATE_A'), findsNothing, reason: 'A 迟到结果覆盖了 B');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('换号清理：退出后登入另一账号，无旧账号哨兵', (tester) async {
    var account = 'A';
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/user/memos') {
        return reply({'total': 1, 'memos': [
          {'id': 1, 'title': null, 'content': 'SYNTH_MEMO_$account',
           'created_at': '2026-10-01T00:00:00', 'updated_at': '2026-10-01T00:00:00'},
        ]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_A'), findsOneWidget);
    await tester.tap(find.text('退出登录'));
    await tester.pumpAndSettle();
    account = 'B';
    await login(tester);
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_MEMO_B'), findsOneWidget);
    expect(find.text('SYNTH_MEMO_A'), findsNothing, reason: '换号后旧账号数据残留');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('零业务写：六模块浏览全程除登录外只有 GET', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('角色日记'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('备忘'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('我的日记'));
    await tester.pumpAndSettle();
    final writes = calls
        .where((c) => c.$1 != 'GET' && !c.$2.endsWith('/login'))
        .toList();
    expect(writes, isEmpty, reason: '只读面产生了业务写请求：$writes');
    await tester.pumpWidget(const SizedBox());
  });

  // ── 返工新增守卫（§2A/§2C/§2D/§2E + §4）────────────────────────────────────

  testWidgets('记忆续页：offset 用原始窗口推进（不重叠）+ 全程显式 read_only=true',
      (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    expect(memQueryLog.single['read_only'], true, reason: '首屏必须显式无维护读取');
    expect(memQueryLog.single['skip'], 0);
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(memQueryLog.map((q) => q['skip']).toList(), [0, 50],
        reason: '续页 offset 必须是原始窗口（50），不是去重后的 _items.length');
    expect(memQueryLog.every((q) => q['read_only'] == true), isTrue,
        reason: '首屏/续页必须一致走无维护读取，不得回退默认维护路径');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('动态：空可见页不是终点，后续窗口仍可达（游标按原始窗口推进）', (tester) async {
    final windows = <int>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/moments') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        windows.add(skip);
        if (skip == 0) return reply({'moments': [], 'total': 0}); // 首窗被过滤成空可见页
        if (skip == 20) {
          return reply({'total': 1, 'moments': [momentJson(21, 'SYNTH_LATER_WINDOW')]});
        }
        return reply({'moments': [], 'total': 0});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    expect(find.text('暂无内容'), findsOneWidget); // 空页如实显示
    expect(find.text('加载更多'), findsOneWidget,
        reason: '空/短可见页之后仍可能有内容，不得当末页把用户堵死');
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_LATER_WINDOW'), findsOneWidget);
    expect(windows, [0, 20], reason: '游标按原始窗口推进，不得回退或取可见条数');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('动态：重复页按 id 去重且游标不回退（继续窗口用原始 skip）', (tester) async {
    final windows = <int>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/moments') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        windows.add(skip);
        if (skip == 0) {
          return reply({'total': 2,
              'moments': [momentJson(10, 'SYNTH_W0'), momentJson(11, 'SYNTH_W0B')]});
        }
        if (skip == 20) {
          return reply({'total': 2,
              'moments': [momentJson(11, 'SYNTH_DUP'), momentJson(12, 'SYNTH_W1')]});
        }
        return reply({'total': 1, 'moments': [momentJson(13, 'SYNTH_W2')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_W1'), findsOneWidget);
    expect(find.text('SYNTH_DUP'), findsNothing, reason: '重复 id 必须去重（不重复渲染）');
    expect(find.text('SYNTH_W0B'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_W2'), findsOneWidget);
    expect(windows, [0, 20, 40], reason: '窗口只能前进：不得回退重取同一窗口');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('401 来自详情里的真实请求：清到登录屏且详情 route 作废', (tester) async {
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path.endsWith('/chain')) return reply({'detail': 'expired'}, 401);
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    await tester.tap(find.text('SYNTH_MEM_0')); // 打开详情：抽屉内 chain 真实请求 401
    await tester.pumpAndSettle();
    expect(find.text('登录'), findsOneWidget, reason: '详情里的 401 必须真清到登录屏');
    expect(find.byType(BottomSheet), findsNothing, reason: '详情 route 必须作废');
    expect(find.text('SYNTH_MEM_0'), findsNothing, reason: '不得留下可返回的旧详情内容');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('草稿按角色归属：reader 切对象后不丢不串号；发送只去当前对象会话', (tester) async {
    final sent = <String>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        return reply({'id': 100 + (o.queryParameters['character_id'] as num).toInt()});
      }
      if (o.path.endsWith('/messages/stream')) {
        sent.add('${o.method} ${o.path}');
        return ResponseBody.fromString('data: {"type":"done"}\n\n', 200,
            headers: {Headers.contentTypeHeader: ['text/event-stream']});
      }
      if (o.path.endsWith('/messages')) {
        return reply({'session_id': 1, 'messages': [], 'total': 0});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A')); // chat：对 A 建会话
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_A_DRAFT');
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_B')); // reader 切到 B：作废 chat 的 A 会话
    await tester.pumpAndSettle();
    await tester.tap(find.text('聊天').first); // 回 chat：按 B 重建会话
    await tester.pumpAndSettle();
    var tf = tester.widget<TextField>(find.byType(TextField).last);
    expect(tf.controller?.text, isEmpty, reason: 'A 的未发草稿不得归到 B');
    await tester.enterText(find.byType(TextField).last, 'SYNTH_B_MSG');
    await tester.tap(find.text('发送'));
    await tester.pumpAndSettle();
    expect(sent, ['POST /api/v1/chat/sessions/102/messages/stream'],
        reason: '发送必须 POST 到**当前对象** B 的会话，不得落到 A 的 101');
    await tester.tap(find.text('SYNTH_A')); // 切回 A：A 的草稿必须还在
    await tester.pumpAndSettle();
    tf = tester.widget<TextField>(find.byType(TextField).last);
    expect(tf.controller?.text, 'SYNTH_A_DRAFT', reason: '导航往返不得静默丢失未发草稿');
    await tester.pumpWidget(const SizedBox());
  });

  // ── 返工二新增守卫（§A 窄屏统一入口 / §B 连续空页）────────────────────────

  testWidgets('窄屏：选择器切 B 后发送只去 B 会话（宽窄共用同一选择入口）+ 草稿归属',
      (tester) async {
    final sent = <String>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/chat/sessions') {
        if (o.method == 'GET') return reply({'sessions': [], 'total': 0});
        return reply({'id': 100 + (o.queryParameters['character_id'] as num).toInt()});
      }
      if (o.path.endsWith('/messages/stream')) {
        sent.add('${o.method} ${o.path}');
        return ResponseBody.fromString('data: {"type":"done"}\n\n', 200,
            headers: {Headers.contentTypeHeader: ['text/event-stream']});
      }
      if (o.path.endsWith('/messages')) {
        return reply({'session_id': 1, 'messages': [], 'total': 0});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('SYNTH_A')); // 宽屏 chat：对 A 建会话 101
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_A_DRAFT_NARROW');
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    tester.view.physicalSize = const Size(390, 844); // 转窄屏：角色选择器＝picker + 抽屉
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A').first); // 窄屏 picker 条 → 弹出选择器
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_B')); // 选择器里选 B：必须走同一处理入口
    await tester.pumpAndSettle();
    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await tester.pumpAndSettle();
    await tester.tap(find.text('聊天').last); // 抽屉 → 聊天
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(Tab, '聊天')); // 窄屏 TabBar 的聊天页
    await tester.pumpAndSettle();
    var tf = tester.widget<TextField>(find.byType(TextField).last);
    expect(tf.controller?.text, isEmpty, reason: 'A 的未发草稿不得归到 B');
    await tester.enterText(find.byType(TextField).last, 'SYNTH_B_NARROW');
    await tester.tap(find.text('发送'));
    await tester.pumpAndSettle();
    expect(sent, ['POST /api/v1/chat/sessions/102/messages/stream'],
        reason: '窄屏选择器必须与宽屏共用同一入口：发送要去 B(102)，不能落到 A(101)');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('窄屏：401 后选择器结果不得复活私有状态', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    tester.view.physicalSize = const Size(390, 844);
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_B'));
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_BIO'), findsWidgets); // B 的详情已按新对象加载
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.onUnauthorized!(); // 401（与既有钩子同一回调）
    await tester.pumpAndSettle();
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('SYNTH_BIO'), findsNothing, reason: '401 后旧对象的私人详情必须消失');
    expect(find.byType(BottomSheet), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('动态：连续两窗为空仍可继续到第三窗（空可见页永不封口）', (tester) async {
    final skips = <int>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/moments') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        skips.add(skip);
        return reply({'total': skip < 40 ? 0 : 1,
            'moments': skip < 40 ? [] : [momentJson(999, 'SYNTH_THIRD_WINDOW')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(skips, [0, 20]);
    expect(find.text('加载更多'), findsOneWidget, reason: '第二个空窗同样不能证明末尾');
    await tester.tap(find.text('加载更多'));
    await tester.pumpAndSettle();
    expect(skips, [0, 20, 40]);
    expect(find.text('SYNTH_THIRD_WINDOW'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('动态：三次连续空窗 + 空→重复→新数据：游标只前进、去重仅影响展示', (tester) async {
    final skips = <int>[];
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/moments') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        skips.add(skip);
        if (skip < 60) return reply({'moments': [], 'total': 0}); // 0/20/40 三窗连续为空
        if (skip == 60) return reply({'total': 1, 'moments': [momentJson(10, 'SYNTH_DUP_ONLY')]});
        return reply({'total': 2,
            'moments': [momentJson(10, 'SYNTH_DUP_AGAIN'), momentJson(11, 'SYNTH_FOURTH')]});
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('动态'));
    await tester.pumpAndSettle();
    for (var i = 0; i < 2; i++) { // 连续两个空窗（第 2/3 窗）
      await tester.tap(find.text('加载更多'));
      await tester.pumpAndSettle();
    }
    expect(skips, [0, 20, 40], reason: '三个空窗（0/20/40）都不得自动停，游标只前进');
    expect(find.text('加载更多'), findsOneWidget);
    await tester.tap(find.text('加载更多')); // 第 4 窗：出现可见内容
    await tester.pumpAndSettle();
    expect(skips, [0, 20, 40, 60]);
    expect(find.text('SYNTH_DUP_ONLY'), findsOneWidget);
    await tester.tap(find.text('加载更多')); // 第 5 窗：重复 + 新
    await tester.pumpAndSettle();
    expect(skips, [0, 20, 40, 60, 80]);
    expect(find.text('SYNTH_FOURTH'), findsOneWidget);
    expect(find.text('SYNTH_DUP_AGAIN'), findsNothing, reason: '重复 id 只影响展示，不得重复渲染');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('记忆：刷新/旧续页晚到不得污染新列表（跨筛选旧页2）', (tester) async {
    final delayed = Completer<ResponseBody>();
    await boot(tester);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.dio.httpClientAdapter = FakeAdapter((o) async {
      if (o.path == '/api/v1/memories') {
        final skip = (o.queryParameters['skip'] as num?)?.toInt() ?? 0;
        if (skip > 0) return delayed.future; // 旧筛选的页2挂起
        if (o.queryParameters['memory_type'] != null) {
          return reply({'total': 1,
              'memories': [memJson(900, 1, 'preference', 'SYNTH_NEW_FILTER')]});
        }
      }
      return synthHandler(o);
    });
    await login(tester);
    await tester.tap(find.text('记忆'));
    await tester.pumpAndSettle();
    await pickCharA(tester);
    await tester.scrollUntilVisible(find.text('加载更多'), 100,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('加载更多')); // skip=50 挂起
    await tester.pump();
    await tester.tap(find.byType(DropdownButton<String?>));
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('preference').last); // 换筛选 = 刷新（作废旧续页）
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('SYNTH_NEW_FILTER'), findsOneWidget);
    delayed.complete(reply({'total': 52,
        'memories': [memJson(901, 1, 'event', 'SYNTH_OLD_PAGE2')]})); // 旧页2 晚到
    await tester.pumpAndSettle();
    expect(find.text('SYNTH_OLD_PAGE2'), findsNothing, reason: '旧筛选的页2晚到不得混入新列表');
    await tester.pumpWidget(const SizedBox());
  });

  // 名称如实反映覆盖：本用例只触发 **401 钩子**（onUnauthorized），不冒称 logout 路径。
  // 未补「选择器仍打开时点退出登录」的单独用例：模态选择器的 barrier 挡住 AppBar，
  // 该交错在 UI 上不可达（退出必须先关选择器），故不虚设一条无法真实执行的用例。
  testWidgets('交错时序：选择器仍打开时 401，返回后不得复活旧私文/角色', (tester) async {
    await boot(tester);
    await login(tester);
    await tester.tap(find.text('SYNTH_A')); // chat 建 A 会话
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'SYNTH_A_DRAFT_INTERLEAVE');
    await tester.tap(find.text('角色详情'));
    await tester.pumpAndSettle();
    tester.view.physicalSize = const Size(390, 844); // 窄屏：picker 变模态选择器
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A').first); // 打开选择器（**不选**）
    await tester.pumpAndSettle();
    expect(find.byType(BottomSheet), findsOneWidget);
    final dynamic state = tester.state(find.byType(app.WorkspacePage));
    state.env.client.onUnauthorized!(); // 选择器仍打开的**交错时刻**触发 401
    await tester.pumpAndSettle();
    expect(find.byType(BottomSheet), findsNothing, reason: '401 必须把仍开着的选择器一并作废');
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('SYNTH_BIO'), findsNothing, reason: '旧角色的私文不得留在可返回位置');
    // 重登后不得复活：对象/私文/草稿都是空的（窄屏走抽屉入口）
    await login(tester);
    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await tester.pumpAndSettle();
    await tester.tap(find.text(ReaderStrings('zh').t('modProfile')));
    await tester.pumpAndSettle();
    expect(find.text(ReaderStrings('zh').t('pickCharacter')), findsWidgets,
        reason: '重登后不得自动复活旧角色');
    expect(find.text('SYNTH_BIO'), findsNothing, reason: '重登后旧角色的私文不得复活');
    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await tester.pumpAndSettle();
    // 抽屉条目用 descendant 定位：'聊天' 同时存在于 TabBar，`.last` 会点错目标
    await tester.tap(find.descendant(
        of: find.byType(Drawer), matching: find.text('聊天')));
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsNothing,
        reason: '重登后未选角色 ⇒ 聊天区无输入框（旧对象未复活）');
    // 再选一次 A：草稿桶已被 401 清空 ⇒ 输入框必须为空
    await tester.tap(find.widgetWithText(Tab, '角色'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('SYNTH_A'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(Tab, '聊天'));
    await tester.pumpAndSettle();
    final tf = tester.widget<TextField>(find.byType(TextField).last);
    expect(tf.controller?.text, isEmpty, reason: '401 后草稿必须清空（不复活旧输入）');
    await tester.pumpWidget(const SizedBox());
  });

  test('作者时区格式化：UTC naive + 偏移（跨日前后）', () {
    expect(formatAuthorTime('2026-01-01T00:30:00', 8), '2026-01-01 08:30');
    expect(formatAuthorTime('2026-01-01T00:30:00', -5), '2025-12-31 19:30');
    expect(formatAuthorTime('bad', 8), 'bad'); // 解析失败回退原文
  });

  test('双语成对：zh/en key 完全对齐且均有值', () {
    final t = ReaderStrings.debugTables;
    expect(t.keys.toSet(), {'zh', 'en'});
    final zh = t['zh']!, en = t['en']!;
    expect(zh.keys.toSet(), en.keys.toSet(), reason: 'zh/en key 不成对');
    for (final k in zh.keys) {
      expect(zh[k]!.isNotEmpty && en[k]!.isNotEmpty, isTrue, reason: k);
    }
  });
}
