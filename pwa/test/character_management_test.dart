// PWA M1a-5 · 角色创建/编辑的长期守卫（控制器 + Widget）。
//
// 用可控 Dio 适配器验证真实请求形状与 UI 状态；不连网络、不用真实模型。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ambrace_pwa/features/character_management/character_editor_sheet.dart';
import 'package:ambrace_pwa/features/character_management/character_editor_state.dart';
import 'package:ambrace_pwa/l10n/character_editor_strings.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.handler);
  final Future<ResponseBody> Function(RequestOptions options) handler;
  final List<RequestOptions> requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) {
    requests.add(options);
    return handler(options);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(Object body, [int status = 200]) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: <String, List<String>>{
        Headers.contentTypeHeader: <String>[Headers.jsonContentType],
      },
    );

const Map<String, dynamic> _charJson = <String, dynamic>{
  'id': 7,
  'user_id': 3,
  'name': 'Neo',
  'bio': 'lives downtown',
  'personality': 'gentle',
  'chat_style': 'casual',
  'greeting_message': 'hi',
  'gender': 'female',
  'appearance': 'short hair',
  'talkativeness': 60,
  'talkativeness_locked': false,
  'timezone_offset': 8,
};

({CharacterEditorApi api, _Adapter adapter}) _make(
  Future<ResponseBody> Function(RequestOptions) handler,
) {
  final dio = Dio(BaseOptions(baseUrl: 'http://unit.invalid'));
  final adapter = _Adapter(handler);
  dio.httpClientAdapter = adapter;
  return (api: CharacterEditorApi(AmbApiClient(dio: dio)), adapter: adapter);
}

void main() {

  group('strings（zh/en 成对）', () {
    test('键集完全一致且值非空', () {
      final zh = CharacterEditorStrings.table['zh']!;
      final en = CharacterEditorStrings.table['en']!;
      expect(zh.keys.toSet(), en.keys.toSet());
      for (final k in zh.keys) {
        expect(zh[k]!.trim(), isNotEmpty, reason: 'zh.$k');
        expect(en[k]!.trim(), isNotEmpty, reason: 'en.$k');
      }
    });
  });

  group('CharacterEditorController（写入纪律）', () {
    test('创建模式 load 不发请求；空名称校验 ⇒ 0 请求', () async {
      final t = _make((_) async => _json(_charJson, 201));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      expect(t.adapter.requests, isEmpty);
      c.draft!.setField('name', '   ');
      expect(await c.save(), isNull);
      expect(c.errorKey, 'name_required');
      expect(t.adapter.requests, isEmpty);
    });

    test('创建成功：恰好 1 次 POST，负载只含白名单字段', () async {
      final t = _make((_) async => _json(_charJson, 201));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      c.draft!.setField('bio', 'lives downtown');
      final saved = await c.save();
      expect(saved?.id, 7);
      expect(t.adapter.requests.length, 1);
      final body = t.adapter.requests.single.data as Map;
      expect(body['name'], 'Neo');
      expect(body.keys.every((k) => kCharacterEditorFields.contains(k)), isTrue);
      expect(c.isDirty, isFalse, reason: '保存成功后 dirty 清空');
    });

    test('★ 同步 saving 锁：并发两次 save 只产生 1 次写', () async {
      final gate = Completer<void>();
      final t = _make((_) async {
        await gate.future;
        return _json(_charJson, 201);
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      final f1 = c.save();
      final f2 = c.save(); // 重入应被锁挡下
      gate.complete();
      await Future.wait(<Future<CharacterEditorData?>>[f1, f2]);
      expect(t.adapter.requests.length, 1);
    });

    test('编辑：GET 预填、PUT 只发差异、清空发空串', () async {
      var putBody = <String, dynamic>{};
      final t = _make((o) async {
        if (o.method == 'GET') return _json(_charJson);
        putBody = (o.data as Map).cast<String, dynamic>();
        return _json({..._charJson, 'bio': ''});
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, characterId: 7, currentUserId: 3,
      );
      await c.load();
      expect(c.draft!['name'], 'Neo');
      expect(c.draft!['bio'], 'lives downtown');
      c.draft!.setField('bio', '');
      await c.save();
      expect(putBody, {'bio': ''});
      expect(t.adapter.requests.map((r) => r.method).toList(), ['GET', 'PUT']);
    });

    test('403 ⇒ forbidden；草稿保留', () async {
      final t = _make((o) async {
        if (o.method == 'GET') return _json(_charJson);
        return _json({'detail': 'forbidden'}, 403);
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, characterId: 7, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('bio', 'changed');
      expect(await c.save(), isNull);
      expect(c.errorKey, 'forbidden');
      expect(c.draft!['bio'], 'changed');
    });

    test('超时/断线 ⇒ unconfirmed（结果未确认），草稿保留', () async {
      final t = _make((o) async {
        if (o.method == 'GET') return _json(_charJson);
        throw DioException(
          requestOptions: o,
          type: DioExceptionType.receiveTimeout,
          message: 'timeout',
        );
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, characterId: 7, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('bio', 'changed');
      expect(await c.save(), isNull);
      expect(c.unconfirmed, isTrue);
      expect(c.errorKey, 'resultUnconfirmed');
      expect(c.draft!['bio'], 'changed');
    });

    test('晚到回调（stale）不污染状态', () async {
      final t = _make((_) async => _json(_charJson));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => true, characterId: 7, currentUserId: 3,
      );
      await c.load();
      expect(c.original, isNull, reason: '过期结果不得落库');
    });
  });

  group('CharacterEditorSheet（Widget）', () {
    Future<void> openSheet(
      WidgetTester tester, {
      required _AdapterHolder holder,
      int? characterId,
      void Function(CharacterEditorData)? onSaved,
    }) async {
      // 表单有 10 个字段：默认 800×600 视口下底部按钮在可视区外，tap 会打不中。
      // 这里只放大**测试画布**（不改断言语义）；窄屏滚动/键盘行为由浏览器矩阵验证。
      tester.view.physicalSize = const Size(800, 2400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () => CharacterEditorSheet.show(
                ctx,
                client: holder.client,
                strings: const CharacterEditorStrings('en'),
                authGen: 1,
                routeGen: 1,
                isStale: (a, r) => false,
                onSaved: onSaved ?? (_) {},
                characterId: characterId,
                currentUserId: 3,
              ),
              child: const Text('open'),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
    }

    testWidgets('空表单创建：填名 → Create → 恰好 1 次 POST', (tester) async {
      final holder = _AdapterHolder(_json(_charJson, 201));
      CharacterEditorData? saved;
      await openSheet(tester, holder: holder, onSaved: (d) => saved = d);

      expect(find.text('Create character'), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, 'Neo');
      final createBtn = find.widgetWithText(FilledButton, 'Create');
      await tester.ensureVisible(createBtn);
      await tester.pumpAndSettle();
      await tester.tap(createBtn);
      await tester.pumpAndSettle();

      expect(holder.adapter.requests.where((r) => r.method == 'POST').length, 1);
      expect(saved?.id, 7);
    });

    testWidgets('dirty 取消 ⇒ 弹出「继续编辑/放弃」，选继续则不关', (tester) async {
      final holder = _AdapterHolder(_json(_charJson, 201));
      await openSheet(tester, holder: holder);
      await tester.enterText(find.byType(TextField).first, 'Neo');
      final cancelBtn = find.widgetWithText(OutlinedButton, 'Cancel');
      await tester.ensureVisible(cancelBtn);
      await tester.pumpAndSettle();
      await tester.tap(cancelBtn);
      await tester.pumpAndSettle();

      expect(find.text('Discard unsaved changes?'), findsOneWidget);
      await tester.tap(find.text('Keep editing'));
      await tester.pumpAndSettle();
      expect(find.text('Create character'), findsOneWidget, reason: '应仍在表单');
    });

    testWidgets('编辑模式：GET 预填字段', (tester) async {
      final holder = _AdapterHolder(_json(_charJson));
      await openSheet(tester, holder: holder, characterId: 7);
      await tester.pumpAndSettle();

      expect(find.text('Edit profile'), findsOneWidget);
      final nameField = tester.widget<TextField>(find.byType(TextField).first);
      expect(nameField.controller!.text, 'Neo');
      expect(holder.adapter.requests.single.method, 'GET');
    });
  });

  group('A 段反例长期化（与主审 independent probe 同断言）', () {
    Future<_AdapterHolder> mountDirect(
      WidgetTester tester,
      _AdapterHolder holder, {
      int? characterId,
      Future<bool> Function()? onVerify,
    }) async {
      tester.view.physicalSize = const Size(800, 2400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: CharacterEditorSheet(
            client: holder.client,
            strings: const CharacterEditorStrings('en'),
            authGen: 1,
            routeGen: 1,
            isStale: (a, r) => false,
            currentUserId: 3,
            characterId: characterId,
            onSaved: (_) {},
            onVerify: onVerify,
          ),
        ),
      ));
      await tester.pumpAndSettle();
      return holder;
    }

    test('★ 未确认结果必须阻断再次 POST', () async {
      final t = _make((o) async =>
          throw DioException(requestOptions: o, type: DioExceptionType.receiveTimeout));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      await c.save();
      expect(c.unconfirmed, isTrue);
      await c.save(); // 未知结果期间的重试必须被挡住
      expect(t.adapter.requests.where((r) => r.method == 'POST').length, 1);
      expect(c.canSubmit, isFalse);
    });

    testWidgets('★ 输入后返回保护立即生效（canPop=false）', (tester) async {
      final holder = _AdapterHolder(_json(_charJson, 201));
      await mountDirect(tester, holder);
      await tester.enterText(find.byType(TextField).at(0), 'Neo');
      await tester.pump();
      final scopes =
          tester.widgetList<PopScope>(find.byWidgetPredicate((w) => w is PopScope));
      expect(scopes.last.canPop, isFalse);
    });

    testWidgets('★ 非法数字不会被另一个合法数字清除（0 请求）', (tester) async {
      final holder = _AdapterHolder(_json(_charJson, 201));
      await mountDirect(tester, holder);
      await tester.enterText(find.byType(TextField).at(0), 'Neo');
      await tester.enterText(find.byType(TextField).at(7), 'oops');
      await tester.enterText(find.byType(TextField).at(8), '8');
      await tester.pump();
      final create = find.widgetWithText(FilledButton, 'Create');
      await tester.ensureVisible(create);
      await tester.tap(create);
      await tester.pumpAndSettle();
      expect(holder.adapter.requests.where((r) => r.method == 'POST'), isEmpty);
    });

    testWidgets('★ 反向顺序同样拦下；改错后按钮恢复且各字段独立', (tester) async {
      final holder = _AdapterHolder(_json(_charJson, 201));
      await mountDirect(tester, holder);
      await tester.enterText(find.byType(TextField).at(0), 'Neo');
      // 先 timezone 非法、再 talk 合法 ⇒ 仍非法
      await tester.enterText(find.byType(TextField).at(8), 'bad');
      await tester.enterText(find.byType(TextField).at(7), '50');
      await tester.pump();
      var create = find.widgetWithText(FilledButton, 'Create');
      await tester.ensureVisible(create);
      await tester.tap(create);
      await tester.pumpAndSettle();
      expect(holder.adapter.requests.where((r) => r.method == 'POST'), isEmpty);
      // 改对 timezone ⇒ 两个字段都合法，按钮恢复可提交
      await tester.enterText(find.byType(TextField).at(8), '8');
      await tester.pump();
      create = find.widgetWithText(FilledButton, 'Create');
      final btn = tester.widget<FilledButton>(create);
      expect(btn.onPressed, isNotNull);
    });

    test('★ 编辑未知结果：只读 GET 核对，不再发 PUT', () async {
      var gets = 0;
      final t = _make((o) async {
        if (o.method == 'GET') {
          gets++;
          // 第一次 load；第二次 verifyByGet 返回服务端已保存后的值
          return _json(gets == 1 ? _charJson : {..._charJson, 'bio': 'changed'});
        }
        throw DioException(requestOptions: o, type: DioExceptionType.receiveTimeout);
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, characterId: 7, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('bio', 'changed');
      await c.save();
      expect(c.unconfirmed, isTrue);
      await c.verifyByGet();
      expect(c.verifyResult, 'verifySaved');
      expect(c.unconfirmed, isFalse);
      expect(t.adapter.requests.where((r) => r.method == 'PUT').length, 1);
      expect(t.adapter.requests.where((r) => r.method == 'GET').length, 2);
      expect(c.draft!['bio'], 'changed', reason: '核对不改草稿');
    });
  });
  group('R4/R5（主审反例长期化）', () {
    Future<_AdapterHolder> mountDirect(
      WidgetTester tester,
      _AdapterHolder holder, {
      int? characterId,
      Future<bool> Function()? onVerify,
    }) async {
      tester.view.physicalSize = const Size(800, 2400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: CharacterEditorSheet(
            client: holder.client,
            strings: const CharacterEditorStrings('en'),
            authGen: 1,
            routeGen: 1,
            isStale: (a, r) => false,
            currentUserId: 3,
            characterId: characterId,
            onSaved: (_) {},
            onVerify: onVerify,
          ),
        ),
      ));
      await tester.pumpAndSettle();
      return holder;
    }

    test('★ R4 201 坏封套：未确认且禁止二次 POST', () async {
      final t = _make((_) async => _json({'ok': true}, 201));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'SYNTH_NEW');
      await c.save();
      await c.save(); // 未知结果期间重试必须被挡
      expect(t.adapter.requests.where((r) => r.method == 'POST').length, 1,
          reason: '坏 201 之后可能已提交');
      expect(c.unconfirmed, isTrue);
    });

    testWidgets('★ R5 只读核对失败：表单与原文都保留、不重复写', (tester) async {
      final holder = _AdapterHolder(_json({'ok': true}, 201));
      var calls = 0;
      await mountDirect(tester, holder, onVerify: () async {
        calls++;
        return false; // 模拟 GET 500
      });
      await tester.enterText(find.byType(TextField).at(0), 'SYNTH_DRAFT');
      final create = find.widgetWithText(FilledButton, 'Create');
      await tester.ensureVisible(create);
      await tester.tap(create);
      await tester.pumpAndSettle();
      expect(holder.adapter.requests.where((r) => r.method == 'POST').length, 1);

      final verify = find.text('Refresh to verify (read-only)');
      expect(verify, findsOneWidget, reason: '未确认时必须给只读核对入口');
      await tester.ensureVisible(verify);
      await tester.tap(verify);
      await tester.pumpAndSettle();

      expect(calls, 1);
      expect(find.byType(CharacterEditorSheet), findsOneWidget,
          reason: '核对失败不得销毁表单');
      expect(tester.widget<TextField>(find.byType(TextField).first).controller!.text,
          'SYNTH_DRAFT', reason: '原文必须还在');
      expect(holder.adapter.requests.where((r) => r.method == 'POST').length, 1,
          reason: '核对只读，不得再写');
    });
  });

  group('切片二：入口/保存中/真实返回/窄屏/权限', () {
    test('★ 同家庭非 owner 仍可编辑（不按「本人」一刀禁）', () async {
      // 角色 owner=9，当前账号 3（家庭共享）
      final t = _make((o) async {
        if (o.method == 'GET') return _json({..._charJson, 'user_id': 9});
        return _json({..._charJson, 'user_id': 9, 'bio': 'changed'});
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, characterId: 7, currentUserId: 3,
      );
      await c.load();
      expect(c.original!.userId, 9);
      expect(c.canSubmit, isTrue, reason: 'owner 非本人不等于不可编辑');
      c.draft!.setField('bio', 'changed');
      final saved = await c.save();
      expect(saved, isNotNull);
      expect(t.adapter.requests.where((r) => r.method == 'PUT').length, 1);
    });
  });

  group('M2 创建幂等键', () {
    test('销毁的表单不再加载、保存或核对', () async {
      for (final editing in [false, true]) {
        final t = _make((_) async => _json(_charJson));
        final c = CharacterEditorController(
          api: t.api, authGen: 1, routeGen: 1,
          isStale: (a, r) => false, currentUserId: 3,
          characterId: editing ? 7 : null,
        );
        await c.load();
        c.draft!.setField('name', 'Neo');
        final before = t.adapter.requests.length;
        c.dispose();
        expect(c.canSubmit, isFalse);
        await c.load();
        await c.save();
        await c.verifyByGet();
        expect(t.adapter.requests.length, before);
      }
    });

    test('过期表单加载和核对前不发 GET', () async {
      final t = _make((_) async => _json(_charJson));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1, characterId: 7,
        isStale: (a, r) => true, currentUserId: 3,
      );
      await c.load();
      await c.verifyByGet();
      expect(t.adapter.requests, isEmpty);
      c.dispose();
    });

    test('销毁后的晚到创建结果不回填表单', () async {
      final gate = Completer<ResponseBody>();
      final t = _make((_) => gate.future);
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      final saving = c.save();
      await Future<void>.delayed(Duration.zero);
      c.dispose();
      gate.complete(_json(_charJson, 201));
      expect(await saving, isNull);
      expect(c.original, isNull);
      expect(c.createIdempotencyKey, isNull);
    });

    test('★ 明确失败后重试：复用同一个键', () async {
      final keys = <String?>[];
      final t = _make((o) async {
        keys.add(o.headers['Idempotency-Key'] as String?);
        if (keys.length == 1) return _json({'detail': 'bad'}, 400);
        return _json(_charJson, 201);
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      await c.save();
      expect(c.unconfirmed, isFalse, reason: '400 是明确拒绝，可重试');
      final k1 = c.createIdempotencyKey;
      await c.save();
      expect(keys.length, 2);
      expect(keys[0], isNotNull);
      expect(keys[0]!.length, 32);
      expect(keys[1], keys[0], reason: '重试必须复用同一个键');
      expect(k1, keys[0]);
      expect(c.createIdempotencyKey, isNull,
          reason: '创建成功后本次意图键应丢弃（下次新建重新生成）');
      expect(c.unconfirmed, isFalse);
    });

    test('★ 超时未确认：只 1 次 POST、键保留、不自动换键', () async {
      final t = _make((o) async =>
          throw DioException(requestOptions: o, type: DioExceptionType.receiveTimeout));
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      await c.save();
      final key = c.createIdempotencyKey;
      expect(key, isNotNull);
      await c.save(); // 未确认期间被挡
      expect(t.adapter.requests.where((r) => r.method == 'POST').length, 1);
      expect(c.createIdempotencyKey, key, reason: '未确认不得换键');
      expect(t.adapter.requests.single.headers['Idempotency-Key'], key,
          reason: '请求头必须带创建键');
    });

    test('★ 409 冲突不旋转 key、保留草稿', () async {
      final keys = <String?>[];
      final t = _make((o) async {
        keys.add(o.headers['Idempotency-Key'] as String?);
        return _json({'detail': 'idempotency_key_payload_mismatch'}, 409);
      });
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => false, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      await c.save();
      final key = c.createIdempotencyKey;
      await c.save();
      expect(keys.toSet().length, 1, reason: '409 不得换键绕过冲突');
      expect(c.createIdempotencyKey, key);
      expect(c.draft!['name'], 'Neo', reason: '冲突保留草稿');
    });

    test('★ 新 controller ⇒ 新键（新意图）', () async {
      String? keyOf() {
        final t = _make((_) async => _json(_charJson, 201));
        final c = CharacterEditorController(
          api: t.api, authGen: 1, routeGen: 1,
          isStale: (a, r) => false, currentUserId: 3,
        );
        return c.createIdempotencyKey;
      }
      // 未保存前还没有键；触发一次失败生成后比较
      final keys = <String?>[];
      for (var i = 0; i < 2; i++) {
        final t = _make((o) async {
          keys.add(o.headers['Idempotency-Key'] as String?);
          return _json({'detail': 'bad'}, 400);
        });
        final c = CharacterEditorController(
          api: t.api, authGen: 1, routeGen: 1,
          isStale: (a, r) => false, currentUserId: 3,
        );
        await c.load();
        c.draft!.setField('name', 'Neo');
        await c.save();
      }
      expect(keys[0], isNotNull);
      expect(keys[1], isNot(keys[0]), reason: '新表单必须是新键');
      expect(keyOf(), isNull, reason: '全新 controller 未保存前不该预先持键');
    });

    test('★ 代次失效：发请求前就挡（0 请求）', () async {
      final t = _make((_) async => _json(_charJson, 201));
      var stale = false;
      final c = CharacterEditorController(
        api: t.api, authGen: 1, routeGen: 1,
        isStale: (a, r) => stale, currentUserId: 3,
      );
      await c.load();
      c.draft!.setField('name', 'Neo');
      stale = true;
      final saved = await c.save();
      expect(saved, isNull);
      expect(t.adapter.requests.where((r) => r.method == 'POST'), isEmpty,
          reason: '旧代次不得用新 token 发创建');
    });
  });
}

/// 小包装：让 Widget 用例拿到同一个 client 与可观测 adapter。
class _AdapterHolder {
  _AdapterHolder(Object response, {int status = 200}) {
    final dio = Dio(BaseOptions(baseUrl: 'http://unit.invalid'));
    adapter = _Adapter((o) async {
      if (o.method == 'GET' || o.method == 'POST' || o.method == 'PUT') {
        return response is ResponseBody ? response : _json(response, status);
      }
      return _json(response, status);
    });
    dio.httpClientAdapter = adapter;
    client = AmbApiClient(dio: dio);
  }

  late final _Adapter adapter;
  late final AmbApiClient client;
}
