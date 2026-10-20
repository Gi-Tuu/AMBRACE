// PWA M1a-5 · 主壳集成守卫（真实 WorkspacePage + 合成 Dio）。
//
// 前 3 例为主审 `stronger_integration_probes.dart` 原断言（空列表入口 0 写 /
// 返回 id 真正选中并把新会话路由到该 id / 创建成功后才出现列表 500）；
// 第 4 例补「非空列表入口」。每例都核对**页面与实际请求清单两端**。
import 'dart:async';

import 'package:dio/dio.dart';

import 'package:ambrace_pwa/features/character_management/character_editor_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'session_workbench_test.dart' as harness;

const Map<String, dynamic> _newChar = <String, dynamic>{
  'id': 99,
  'user_id': 1,
  'name': 'SYNTH_NEW',
};

Future<void> _openCreateSheet(WidgetTester t) async {
  await t.tap(find.text('创建角色').first);
  await t.pumpAndSettle();
  expect(find.byType(CharacterEditorSheet), findsOneWidget);
}

Future<void> _submitCreate(WidgetTester t, String name) async {
  final editor = find.byType(CharacterEditorSheet);
  await t.enterText(
      find.descendant(of: editor, matching: find.byType(TextField)).first,
      name);
  final create = find.descendant(
      of: editor, matching: find.widgetWithText(FilledButton, '创建'));
  final outer =
      find.ancestor(of: create, matching: find.byType(Scrollable)).first;
  await t.scrollUntilVisible(create, 180, scrollable: outer, maxScrolls: 40);
  await t.pumpAndSettle();
  await t.ensureVisible(create);
  await t.pumpAndSettle();
  expect(t.widget<FilledButton>(create).onPressed, isNotNull,
      reason: '身份就绪且名称合法时创建按钮应可用');
  expect(create.hitTestable(), findsOneWidget, reason: '创建按钮必须在可视区内');
  await t.tap(create);
  await t.pumpAndSettle();
}

int _posts(String path) =>
    harness.calls.where((c) => c.$1 == 'POST' && c.$2 == path).length;

Map<String, dynamic> _editorData({int owner = 1}) => {
      'id': 1,
      'user_id': owner,
      'name': 'SYNTH_A',
      'bio': 'SYNTH_BIO',
      'personality': 'SYNTH_PERSONALITY',
      'chat_style': 'SYNTH_STYLE',
      'greeting_message': 'SYNTH_GREETING',
      'gender': 'SYNTH_GENDER',
      'appearance': 'SYNTH_APPEARANCE',
      'talkativeness': 60,
      'talkativeness_locked': false,
      'timezone_offset': 8,
      'created_at': '2026-01-01T00:00:00',
    };
Finder _editorField(String label) => find.descendant(
    of: find.byType(CharacterEditorSheet),
    matching: find.byWidgetPredicate(
        (w) => w is TextField && w.decoration?.labelText == label));
Future<void> _editField(WidgetTester t, String label, String value) async {
  final field = _editorField(label);
  await t.ensureVisible(field);
  await t.pumpAndSettle();
  await t.enterText(field, value);
  await t.pump();
}

Future<void> _editorTap(WidgetTester t, String label,
    {bool settle = true}) async {
  final target = find.descendant(
      of: find.byType(CharacterEditorSheet), matching: find.text(label));
  final scroll =
      find.ancestor(of: target, matching: find.byType(Scrollable)).first;
  await t.scrollUntilVisible(target, 180, scrollable: scroll, maxScrolls: 40);
  await t.ensureVisible(target);
  await t.pump();
  expect(target.hitTestable(), findsOneWidget);
  await t.tap(target);
  if (settle) {
    await t.pumpAndSettle();
  } else {
    await t.pump();
    await t.pump(const Duration(milliseconds: 50));
  }
}

Future<void> _openEdit(WidgetTester t) async {
  await t.tap(find.text('SYNTH_A').first);
  await t.pumpAndSettle();
  await t.tap(find.text('角色详情'));
  await t.pumpAndSettle();
  await t.tap(find.widgetWithText(OutlinedButton, '编辑资料'));
  await t.pumpAndSettle();
  expect(find.byType(CharacterEditorSheet), findsOneWidget);
}

List<RequestOptions> _writes() =>
    harness.reqs.where((o) => o.method == 'PUT' || o.method == 'POST').toList();

void main() {
  setUp(() {
    harness.calls.clear();
    harness.reqs.clear();
  });

  testWidgets('空列表：打开创建表单 0 写', (t) async {
    final state = await harness.boot(t);
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters' && o.method == 'GET') {
        return harness
            .reply(<String, dynamic>{'characters': <Map<String, dynamic>>[]});
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    harness.calls.clear();
    await _openCreateSheet(t);
    expect(harness.calls.where((x) => x.$1 == 'POST'), isEmpty);
  });

  testWidgets('非空列表：创建入口存在且打开表单 0 写', (t) async {
    await harness.boot(t);
    await harness.login(t);
    expect(find.text('创建角色'), findsWidgets);
    harness.calls.clear();
    await _openCreateSheet(t);
    expect(_posts('/api/v1/characters'), 0, reason: '打开表单不得发写请求');
  });

  testWidgets('返回 id 被真正选中，新会话归属该 id 且读其历史', (t) async {
    final state = await harness.boot(t);
    var created = false;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters' && o.method == 'POST') {
        created = true;
        return harness.reply(_newChar, 201);
      }
      if (o.path == '/api/v1/characters' && o.method == 'GET') {
        return harness.reply({
          'characters': [
            {'id': 1, 'name': 'SYNTH_A'},
            if (created) _newChar,
          ]
        });
      }
      if (o.path == '/api/v1/chat/sessions' &&
          o.method == 'POST' &&
          '${o.queryParameters['character_id']}' == '99') {
        return harness.reply({'id': 991});
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    // 新角色不得在种子列表里提前出现
    expect(find.text('SYNTH_NEW'), findsNothing);
    harness.calls.clear();
    harness.reqs.clear();
    await _openCreateSheet(t);
    await _submitCreate(t, 'SYNTH_NEW');

    expect(_posts('/api/v1/characters'), 1);
    expect(
        t.widget<ListTile>(find.widgetWithText(ListTile, 'SYNTH_NEW')).selected,
        isTrue,
        reason: '必须选中服务端返回的 id，而不是旧对象');
    final sessions = harness.reqs
        .where((o) => o.method == 'POST' && o.path == '/api/v1/chat/sessions')
        .toList();
    expect(sessions, hasLength(1));
    expect('${sessions.single.queryParameters['character_id']}', '99');
    expect(
        harness.calls, contains(('GET', '/api/v1/chat/sessions/991/messages')));
    // 回到最初的负断言：进入聊天**不得自动发消息**（按真实发送端点判）
    expect(
      harness.calls.where((x) => x.$1 == 'POST' && x.$2.endsWith('/messages')),
      isEmpty,
      reason: '创建后进入聊天不得自动发送任何消息',
    );
  });

  testWidgets('创建成功后才出现的列表 500：只 GET 核对且不二次写', (t) async {
    final state = await harness.boot(t);
    var created = false;
    var failedReads = 0;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters' && o.method == 'POST') {
        created = true;
        return harness.reply(_newChar, 201);
      }
      if (o.path == '/api/v1/characters' && o.method == 'GET' && created) {
        failedReads++;
        return harness.reply({'detail': 'SYNTH_FAILURE'}, 500);
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    harness.calls.clear();
    await _openCreateSheet(t);
    await _submitCreate(t, 'SYNTH_NEW');

    expect(failedReads, 1, reason: '刷新失败确实发生在创建成功之后');
    expect(_posts('/api/v1/characters'), 1, reason: '刷新失败不得诱导再次创建');
    expect(find.byType(CharacterEditorSheet), findsNothing);
    expect(
        harness.calls
            .where((x) => x.$1 == 'POST' && x.$2 == '/api/v1/chat/sessions'),
        isEmpty,
        reason: '列表未确认时不得凭空建会话');
  });
  for (final owner in [1, 42]) {
    testWidgets('A owner $owner 编辑十字段差异清空且聊天不变', (t) async {
      final state = await harness.boot(t);
      var data = _editorData(owner: owner);
      state.env.client.dio.httpClientAdapter =
          harness.loggingAdapter((o) async {
        if (o.path == '/api/v1/characters/1') {
          if (o.method == 'PUT') {
            data = {...data, ...Map<String, dynamic>.from(o.data as Map)};
          }
          return harness.reply(data);
        }
        if (o.path == '/api/v1/chat/sessions/101/messages') {
          return harness.reply({
            'session_id': 101,
            'total': 1,
            'messages': [harness.msgJson(1, 101, 'SYNTH_OLD_HISTORY')]
          });
        }
        return harness.synthHandler(o);
      });
      await harness.login(t);
      await t.tap(find.text('SYNTH_A'));
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField).last, 'SYNTH_OLD_CHAT_DRAFT');
      await t.tap(find.text('角色详情'));
      await t.pumpAndSettle();
      harness.reqs.clear();
      harness.calls.clear();
      await t.tap(find.widgetWithText(OutlinedButton, '编辑资料'));
      await t.pumpAndSettle();
      expect(_writes(), isEmpty);
      final expected = {
        '名称': 'SYNTH_A',
        '简介': 'SYNTH_BIO',
        '性格': 'SYNTH_PERSONALITY',
        '说话风格': 'SYNTH_STYLE',
        '开场白': 'SYNTH_GREETING',
        '性别': 'SYNTH_GENDER',
        '外貌': 'SYNTH_APPEARANCE',
        '话痨程度': '60',
        '时区偏移': '8'
      };
      for (final e in expected.entries) {
        expect(
            t.widget<TextField>(_editorField(e.key)).controller!.text, e.value);
      }
      expect(
          t.widget<SwitchListTile>(find.byType(SwitchListTile)).value, isFalse);
      await _editorTap(t, '取消');
      expect(_writes(), isEmpty);
      await t.tap(find.widgetWithText(OutlinedButton, '编辑资料'));
      await t.pumpAndSettle();
      await _editField(t, '简介', '');
      await _editField(t, '话痨程度', '');
      await _editField(t, '时区偏移', '');
      await _editField(t, '开场白', 'SYNTH_MANUAL');
      final toggle = find.byType(SwitchListTile);
      await t.ensureVisible(toggle);
      await t.pumpAndSettle();
      await t.tap(toggle);
      await t.pump();
      await _editorTap(t, '保存');
      expect(find.byType(CharacterEditorSheet), findsNothing);
      final writes = _writes();
      expect(writes, hasLength(1));
      expect(writes.single.method, 'PUT');
      expect(writes.single.path, '/api/v1/characters/1');
      expect(writes.single.data, {
        'bio': '',
        'talkativeness': null,
        'timezone_offset': null,
        'greeting_message': 'SYNTH_MANUAL',
        'talkativeness_locked': true
      });
      expect(data['user_id'], owner);
      expect(data['id'], 1);
      await t.tap(find.text('聊天'));
      await t.pumpAndSettle();
      expect(find.text('SYNTH_OLD_HISTORY'), findsOneWidget);
      expect(harness.inputText(t), 'SYNTH_OLD_CHAT_DRAFT');
      await t.tap(find.text('SYNTH_A'));
      await t.pumpAndSettle();
      expect(harness.inputText(t), 'SYNTH_OLD_CHAT_DRAFT',
          reason: '编辑资料后再次选中相同角色也须保留同会话草稿');
      expect(
          harness.calls
              .where((c) => c.$1 == 'POST' && c.$2.endsWith('/messages')),
          isEmpty);
    });
  }
  testWidgets('B PUT真实401清dirty modal并重登B不见A', (t) async {
    final state = await harness.boot(t);
    var accountB = false;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters/1') {
        return o.method == 'PUT'
            ? harness.reply({'detail': 'SYNTH_401'}, 401)
            : harness.reply(_editorData());
      }
      if (accountB && o.path == '/api/v1/auth/profile') {
        return harness.reply({'id': 2});
      }
      if (accountB && o.path == '/api/v1/characters') {
        return harness.reply({
          'characters': [
            {'id': 2, 'name': 'SYNTH_ACCOUNT_B'}
          ]
        });
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    await _openEdit(t);
    await _editField(t, '简介', 'SYNTH_A_PRIVATE_DIRTY');
    harness.reqs.clear();
    await _editorTap(t, '保存');
    expect(_writes(), hasLength(1));
    expect(_writes().single.method, 'PUT');
    expect(find.byType(CharacterEditorSheet), findsNothing);
    expect(find.text('SYNTH_A_PRIVATE_DIRTY'), findsNothing);
    expect(find.text('登录'), findsOneWidget);
    accountB = true;
    await harness.login(t);
    expect(find.text('SYNTH_ACCOUNT_B'), findsOneWidget);
    expect(find.text('SYNTH_A'), findsNothing);
    expect(find.text('SYNTH_A_PRIVATE_DIRTY'), findsNothing);
    expect(find.byType(CharacterEditorSheet), findsNothing);
  });
  for (final saved in [true, false]) {
    testWidgets('D 编辑未知PUT后二次GET核对可见结果 saved=$saved', (t) async {
      final state = await harness.boot(t);
      var unknown = false;
      var reads = 0;
      state.env.client.dio.httpClientAdapter =
          harness.loggingAdapter((o) async {
        if (o.path == '/api/v1/characters/1') {
          if (o.method == 'PUT') {
            unknown = true;
            throw DioException(
                requestOptions: o, type: DioExceptionType.receiveTimeout);
          }
          if (unknown) {
            reads++;
            if (reads == 1) {
              return harness.reply({'detail': 'SYNTH_VERIFY_FAILURE'}, 500);
            }
            return harness.reply({
              ..._editorData(),
              'bio': saved ? 'SYNTH_UNKNOWN_DRAFT' : 'SYNTH_BIO'
            });
          }
          return harness.reply(_editorData());
        }
        return harness.synthHandler(o);
      });
      await harness.login(t);
      await _openEdit(t);
      await _editField(t, '简介', 'SYNTH_UNKNOWN_DRAFT');
      harness.reqs.clear();
      await _editorTap(t, '保存');
      expect(_writes(), hasLength(1));
      expect(
          t
              .widget<FilledButton>(find.widgetWithText(FilledButton, '保存'))
              .onPressed,
          isNull);
      await _editorTap(t, '刷新核对（只读）');
      expect(find.text('核对失败，请稍后重试'), findsOneWidget);
      expect(t.widget<TextField>(_editorField('简介')).controller!.text,
          'SYNTH_UNKNOWN_DRAFT');
      await _editorTap(t, '刷新核对（只读）');
      expect(reads, 2);
      expect(_writes(), hasLength(1));
      expect(find.text(saved ? '核对结果：服务端已保存' : '核对结果：服务端尚未保存，可继续修改后明确再保存'),
          findsOneWidget);
      expect(t.widget<TextField>(_editorField('简介')).controller!.text,
          'SYNTH_UNKNOWN_DRAFT');
    });
  }
  for (final kind in ['GET', 'PUT']) {
    for (final status in [200, 500]) {
      testWidgets('C 旧$kind 晚到$status不污染401后新账号与草稿', (t) async {
        final state = await harness.boot(t);
        final gate = Completer<ResponseBody>();
        var accountB = false;
        var delay = false;
        state.env.client.dio.httpClientAdapter =
            harness.loggingAdapter((o) async {
          if (o.path == '/api/v1/synthetic-expire') {
            return harness.reply({'detail': 'SYNTH_EXPIRE'}, 401);
          }
          if (accountB && o.path == '/api/v1/auth/profile') {
            return harness.reply({'id': 2});
          }
          if (accountB && o.path == '/api/v1/characters') {
            return harness.reply({
              'characters': [
                {'id': 2, 'name': 'SYNTH_ACCOUNT_B'}
              ]
            });
          }
          if (o.path == '/api/v1/characters/1') {
            if (delay && o.method == kind) return gate.future;
            return harness.reply(_editorData());
          }
          return harness.synthHandler(o);
        });
        await harness.login(t);
        if (kind == 'GET') {
          await t.tap(find.text('SYNTH_A'));
          await t.pumpAndSettle();
          await t.tap(find.text('角色详情'));
          await t.pumpAndSettle();
          delay = true;
          await t.tap(find.widgetWithText(OutlinedButton, '编辑资料'));
          await t.pump();
          await t.pump(const Duration(milliseconds: 400));
        } else {
          await _openEdit(t);
          await _editField(t, '简介', 'SYNTH_A_OLD_PENDING');
          delay = true;
          harness.reqs.clear();
          await _editorTap(t, '保存', settle: false);
          expect(_writes(), hasLength(1));
          for (final field in t.widgetList<TextField>(find.descendant(
              of: find.byType(CharacterEditorSheet),
              matching: find.byType(TextField)))) {
            expect(field.enabled, isFalse);
          }
          expect(
              t
                  .widget<FilledButton>(
                      find.widgetWithText(FilledButton, '保存中…'))
                  .onPressed,
              isNull);
        }
        // 另一在飞请求真实返回401，走壳统一清理而不是直接reset。
        var expired = false;
        state.env.client.dio
            .get('/api/v1/synthetic-expire')
            .then<void>((_) => fail('应返回401'), onError: (Object error) {
          expired = true;
        });
        await t.pumpAndSettle();
        expect(expired, isTrue);
        expect(find.byType(CharacterEditorSheet), findsNothing);
        accountB = true;
        await harness.login(t);
        await t.tap(find.text('SYNTH_ACCOUNT_B'));
        await t.pumpAndSettle();
        await t.enterText(find.byType(TextField).last, 'SYNTH_B_NEW_DRAFT');
        final before = harness.calls.length;
        gate.complete(harness.reply(
            status == 200
                ? {..._editorData(), 'bio': 'SYNTH_A_LATE_RESULT'}
                : {'detail': 'SYNTH_A_OLD_FAILURE'},
            status));
        await t.pumpAndSettle();
        expect(find.text('SYNTH_ACCOUNT_B'), findsWidgets);
        expect(harness.inputText(t), 'SYNTH_B_NEW_DRAFT');
        expect(find.byType(CharacterEditorSheet), findsNothing);
        expect(find.textContaining('SYNTH_A_LATE_RESULT'), findsNothing);
        expect(find.textContaining('SYNTH_A_OLD_FAILURE'), findsNothing);
        expect(harness.calls.sublist(before), isEmpty,
            reason: '晚到结果不得刷新旧角色或新建错误会话');
      });
    }
  }

  testWidgets('D 编辑GET核对进行中锁定且只读防重入', (t) async {
    final state = await harness.boot(t);
    final gate = Completer<ResponseBody>();
    var unknown = false;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters/1') {
        if (o.method == 'PUT') {
          unknown = true;
          throw DioException(
              requestOptions: o, type: DioExceptionType.receiveTimeout);
        }
        return unknown ? gate.future : harness.reply(_editorData());
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    await _openEdit(t);
    await _editField(t, '简介', 'SYNTH_VERIFY_DRAFT');
    harness.reqs.clear();
    await _editorTap(t, '保存');
    final verify = find.widgetWithText(OutlinedButton, '刷新核对（只读）');
    // 保留原回调测试防重入，不靠按钮变灰代替锁。
    final oldCallback = t.widget<OutlinedButton>(verify).onPressed;
    await _editorTap(t, '刷新核对（只读）', settle: false);
    expect(find.text('核对中…'), findsOneWidget);
    for (final field in t.widgetList<TextField>(find.descendant(
        of: find.byType(CharacterEditorSheet),
        matching: find.byType(TextField)))) {
      expect(field.enabled, isFalse);
    }
    oldCallback?.call();
    await t.pump();
    expect(
        harness.reqs.where(
            (o) => o.method == 'GET' && o.path == '/api/v1/characters/1'),
        hasLength(1));
    gate.complete(
        harness.reply({..._editorData(), 'bio': 'SYNTH_VERIFY_DRAFT'}));
    await t.pumpAndSettle();
    expect(find.text('核对结果：服务端已保存'), findsOneWidget);
    expect(_writes(), hasLength(1));
  });

  testWidgets('D 创建未知结果GET失败后再核对同名不猜id', (t) async {
    final state = await harness.boot(t);
    var unknown = false;
    var reads = 0;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters' && o.method == 'POST') {
        unknown = true;
        throw DioException(
            requestOptions: o, type: DioExceptionType.receiveTimeout);
      }
      if (o.path == '/api/v1/characters' && o.method == 'GET' && unknown) {
        reads++;
        if (reads == 1) {
          return harness.reply({'detail': 'SYNTH_FIRST_FAILED'}, 500);
        }
        return harness.reply({
          'characters': [
            {'id': 81, 'name': 'SYNTH_DUPLICATE'},
            {'id': 82, 'name': 'SYNTH_DUPLICATE'}
          ]
        });
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    harness.reqs.clear();
    await _openCreateSheet(t);
    await _submitCreate(t, 'SYNTH_DUPLICATE');
    await _editorTap(t, '刷新核对（只读）');
    expect(find.text('核对失败，请稍后重试'), findsOneWidget);
    expect(t.widget<TextField>(_editorField('名称')).controller!.text,
        'SYNTH_DUPLICATE');
    await _editorTap(t, '刷新核对（只读）');
    expect(reads, 2);
    expect(find.text('列表已刷新：请确认是否已创建（不要重复创建）'), findsOneWidget);
    expect(find.byType(CharacterEditorSheet), findsOneWidget);
    expect(_writes(), hasLength(1));
    expect(_writes().single.path, '/api/v1/characters');
    expect(
        harness.calls
            .where((c) => c.$1 == 'POST' && c.$2 == '/api/v1/chat/sessions'),
        isEmpty);
    expect(
        t
            .widget<FilledButton>(find.widgetWithText(FilledButton, '创建'))
            .onPressed,
        isNull);
  });
  for (final operation in ['PUT', 'VERIFY']) {
    for (final status in [200, 500]) {
      testWidgets('C 同账号切角色后旧$operation 晚到$status不覆盖新对象', (t) async {
        final state = await harness.boot(t);
        final gate = Completer<ResponseBody>();
        var pending = false;
        state.env.client.dio.httpClientAdapter =
            harness.loggingAdapter((o) async {
          if (o.path == '/api/v1/characters/2') {
            return harness.reply({
              ..._editorData(),
              'id': 2,
              'name': 'SYNTH_B',
              'bio': 'SYNTH_NEW_ROLE'
            });
          }
          if (o.path == '/api/v1/characters/1') {
            if (operation == 'PUT' && o.method == 'PUT') return gate.future;
            if (operation == 'VERIFY' && o.method == 'PUT') {
              pending = true;
              throw DioException(
                  requestOptions: o, type: DioExceptionType.receiveTimeout);
            }
            if (operation == 'VERIFY' && pending) return gate.future;
            return harness.reply(_editorData());
          }
          return harness.synthHandler(o);
        });
        await harness.login(t);
        await _openEdit(t);
        await _editField(t, '简介', 'SYNTH_OLD_ROLE_DRAFT');
        if (operation == 'VERIFY') {
          await _editorTap(t, '保存');
          await _editorTap(t, '刷新核对（只读）', settle: false);
        } else {
          await _editorTap(t, '保存', settle: false);
        }
        // 模拟壳收到对象选择事件；使用真实ListTile回调，未直接改controller或代次。
        t.widget<ListTile>(find.widgetWithText(ListTile, 'SYNTH_B')).onTap!();
        await t.pumpAndSettle();
        expect(find.byType(CharacterEditorSheet), findsNothing);
        expect(find.text('SYNTH_NEW_ROLE'), findsOneWidget);
        await t.tap(find.text('聊天'));
        await t.pumpAndSettle();
        await t.enterText(find.byType(TextField).last, 'SYNTH_NEW_ROLE_CHAT');
        final before = harness.calls.length;
        gate.complete(harness.reply(
            status == 200
                ? {..._editorData(), 'bio': 'SYNTH_OLD_LATE'}
                : {'detail': 'SYNTH_OLD_LATE_ERROR'},
            status));
        await t.pumpAndSettle();
        expect(
            t
                .widget<ListTile>(find.widgetWithText(ListTile, 'SYNTH_B'))
                .selected,
            isTrue);
        expect(harness.inputText(t), 'SYNTH_NEW_ROLE_CHAT');
        expect(harness.calls.sublist(before), isEmpty);
        expect(find.byType(CharacterEditorSheet), findsNothing);
      });
    }
  }
  for (final status in [200, 500]) {
    testWidgets('D 创建只读核对晚到$status不污染重登账号', (t) async {
      final state = await harness.boot(t);
      final gate = Completer<ResponseBody>();
      var unknown = false;
      var accountB = false;
      state.env.client.dio.httpClientAdapter =
          harness.loggingAdapter((o) async {
        if (o.path == '/api/v1/synthetic-expire') {
          return harness.reply({'detail': 'SYNTH_EXPIRE'}, 401);
        }
        if (accountB && o.path == '/api/v1/auth/profile') {
          return harness.reply({'id': 2});
        }
        if (accountB && o.path == '/api/v1/characters') {
          return harness.reply({
            'characters': [
              {'id': 2, 'name': 'SYNTH_ACCOUNT_B'}
            ]
          });
        }
        if (o.path == '/api/v1/characters' && o.method == 'POST') {
          unknown = true;
          throw DioException(
              requestOptions: o, type: DioExceptionType.receiveTimeout);
        }
        if (unknown && o.path == '/api/v1/characters' && o.method == 'GET') {
          return gate.future;
        }
        return harness.synthHandler(o);
      });
      await harness.login(t);
      await _openCreateSheet(t);
      await _submitCreate(t, 'SYNTH_A_CREATE_DIRTY');
      await _editorTap(t, '刷新核对（只读）', settle: false);
      state.env.client.dio
          .get('/api/v1/synthetic-expire')
          .then<void>((_) => fail('应401'), onError: (Object e) {});
      await t.pumpAndSettle();
      expect(find.byType(CharacterEditorSheet), findsNothing);
      accountB = true;
      await harness.login(t);
      final before = harness.calls.length;
      gate.complete(harness.reply(
          status == 200
              ? {
                  'characters': [
                    {'id': 99, 'name': 'SYNTH_A_CREATE_LATE'}
                  ]
                }
              : {'detail': 'SYNTH_A_ERROR'},
          status));
      await t.pumpAndSettle();
      expect(find.text('SYNTH_ACCOUNT_B'), findsOneWidget);
      expect(find.textContaining('SYNTH_A_CREATE'), findsNothing);
      expect(find.byType(CharacterEditorSheet), findsNothing);
      expect(harness.calls.sublist(before), isEmpty);
    });
  }

  testWidgets('创建的实际 POST 带合法幂等键；新表单换新键', (t) async {
    final state = await harness.boot(t);
    final keys = <String?>[];
    var created = 0;
    state.env.client.dio.httpClientAdapter = harness.loggingAdapter((o) async {
      if (o.path == '/api/v1/characters' && o.method == 'POST') {
        keys.add(o.headers['Idempotency-Key'] as String?);
        created++;
        return harness.reply(_newChar, 201);
      }
      if (o.path == '/api/v1/characters' && o.method == 'GET') {
        return harness.reply({
          'characters': [
            {'id': 1, 'name': 'SYNTH_A'},
            if (created > 0) _newChar,
          ]
        });
      }
      return harness.synthHandler(o);
    });
    await harness.login(t);
    await _openCreateSheet(t);
    await _submitCreate(t, 'K_ONE');
    await _openCreateSheet(t);
    await _submitCreate(t, 'K_TWO');

    expect(keys.length, 2, reason: '两次创建各一次 POST');
    for (final k in keys) {
      expect(k, isNotNull, reason: 'PWA 实际创建路径必须带键');
      expect(k!.length, 32, reason: '32 位 hex');
      expect(RegExp(r'^[0-9a-f]{32}$').hasMatch(k), isTrue);
    }
    expect(keys[1], isNot(keys[0]), reason: '新表单必须是新键');
  });
}
