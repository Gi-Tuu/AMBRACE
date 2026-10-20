// PWA M1a-5 core 层单测：角色创建/编辑的 path/method/lang、字段白名单、
// 创建/编辑解析、严格坏响应、目标与归属一致性、空值 vs 省略、无改动不写。
//
// 用**可控适配器**（记录 RequestOptions、返回固定封套）验证真实请求形状；
// 不连网络、不依赖 PWA/Flutter UI。
import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.handler);

  final ResponseBody Function(RequestOptions options) handler;
  final List<RequestOptions> requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
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
  'name': '小爱',
  'bio': '住在城南',
  'personality': '温柔',
  'chat_style': '自然',
  'greeting_message': '你来啦',
  'gender': 'female',
  'appearance': '短发',
  'talkativeness': 60,
  'talkativeness_locked': false,
  'timezone_offset': 8,
  // 不该进 DTO 的字段：解析结果必须没有它们
  'system_prompt': 'TOP_SECRET_PROMPT',
  'user_llm_config_id': 42,
};

({CharacterEditorApi api, _FakeAdapter adapter, AmbApiClient client}) _make(
  ResponseBody Function(RequestOptions) handler,
) {
  final dio = Dio(BaseOptions(baseUrl: 'http://unit.invalid'));
  final adapter = _FakeAdapter(handler);
  dio.httpClientAdapter = adapter;
  final client = AmbApiClient(dio: dio);
  return (api: CharacterEditorApi(client), adapter: adapter, client: client);
}

void main() {
  group('CharacterEditorData.fromJson（严格解析）', () {
    test('取白名单字段并忽略 system_prompt / 模型配置', () {
      final d = CharacterEditorData.fromJson(_charJson);
      expect(d.id, 7);
      expect(d.userId, 3);
      expect(d.name, '小爱');
      expect(d.bio, '住在城南');
      expect(d.talkativeness, 60);
      expect(d.timezoneOffset, 8);
      // 不存在技术字段
      expect(d.toString(), isNot(contains('TOP_SECRET_PROMPT')));
    });

    test('缺 id / user_id / name ⇒ FormatException', () {
      for (final key in <String>['id', 'user_id', 'name']) {
        final m = Map<String, dynamic>.from(_charJson)..remove(key);
        expect(() => CharacterEditorData.fromJson(m), throwsFormatException, reason: key);
      }
    });

    test('类型错 ⇒ FormatException（空 name / 非 int / 非 bool）', () {
      expect(() => CharacterEditorData.fromJson({..._charJson, 'name': '  '}),
          throwsFormatException);
      expect(() => CharacterEditorData.fromJson({..._charJson, 'talkativeness': '60'}),
          throwsFormatException);
      expect(() => CharacterEditorData.fromJson({..._charJson, 'talkativeness_locked': 1}),
          throwsFormatException);
    });

    test('归属核对：expectedId / expectedUserId 不符 ⇒ FormatException', () {
      expect(() => CharacterEditorData.fromJson(_charJson, expectedId: 8),
          throwsFormatException);
      expect(() => CharacterEditorData.fromJson(_charJson, expectedUserId: 9),
          throwsFormatException);
      expect(CharacterEditorData.fromJson(_charJson, expectedId: 7, expectedUserId: 3).id, 7);
    });
  });

  group('CharacterDraft（校验 / 负载）', () {
    test('空名称、超长、数值越界均被本地校验拦下', () {
      final d = CharacterDraft.blank()..setField('name', '   ');
      expect(d.validate(), 'name_required');
      d.setField('name', 'x' * 101);
      expect(d.validate(), 'name_too_long');
      d.setField('name', 'ok');
      d.setField('talkativeness', 101);
      expect(d.validate(), 'talkativeness_out_of_range');
      d.setField('talkativeness', 50);
      d.setField('timezone_offset', 15);
      expect(d.validate(), 'timezone_out_of_range');
      d.setField('timezone_offset', 8);
      expect(d.validate(), isNull);
    });

    test('创建负载含名称与已填字段，null 字段省略', () {
      final d = CharacterDraft.blank()
        ..setField('name', '  小爱  ')
        ..setField('bio', '住在城南')
        ..setField('talkativeness', 60);
      final payload = d.toCreatePayload();
      expect(payload['name'], '小爱'); // trim
      expect(payload['bio'], '住在城南');
      expect(payload['talkativeness'], 60);
      expect(payload.containsKey('personality'), isFalse);
    });

    test('编辑负载只含 touched；未改字段整字段省略', () {
      final data = CharacterEditorData.fromJson(_charJson);
      final d = data.toDraft()..setField('bio', '搬到城北');
      final payload = d.toUpdatePayload(data);
      expect(payload, {'bio': '搬到城北'});
      expect(payload.containsKey('name'), isFalse);
    });

    test('主动清空文本 ⇒ 发空串；清空数值 ⇒ 发 null（与「未改」区分）', () {
      final data = CharacterEditorData.fromJson(_charJson);
      final d = data.toDraft()
        ..setField('bio', '')
        ..setField('talkativeness', null);
      expect(d.toUpdatePayload(data), {'bio': '', 'talkativeness': null});
    });

    test('touched 但值与原始相同 ⇒ 不产生负载（避免无意义写）', () {
      final data = CharacterEditorData.fromJson(_charJson);
      final d = data.toDraft()..setField('bio', '住在城南');
      expect(d.toUpdatePayload(data), isEmpty);
    });

    test('非白名单字段直接拒绝', () {
      final d = CharacterDraft.blank();
      expect(() => d.setField('system_prompt', 'x'), throwsArgumentError);
    });
  });

  group('CharacterEditorApi（真实请求形状）', () {
    test('load：GET /api/v1/characters/{id}，lang 走请求头', () async {
      final t = _make((_) => _json(_charJson));
      final d = await t.api.load(7, lang: 'en');
      expect(t.adapter.requests.single.method, 'GET');
      expect(t.adapter.requests.single.path, '/api/v1/characters/7');
      expect(t.adapter.requests.single.headers['lang'], 'en');
      expect(d.name, '小爱');
    });

    test('load 坏响应（数组）⇒ ApiException unknown', () async {
      final t = _make((_) => _json(<dynamic>[]));
      expect(
        () => t.api.load(7),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });

    test('create：POST /api/v1/characters，负载只含白名单字段', () async {
      final t = _make((_) => _json(_charJson, 201));
      final draft = CharacterDraft.blank()
        ..setField('name', '小爱')
        ..setField('bio', '住在城南')
        ..setField('talkativeness', 60);
      final d = await t.api.create(draft, expectedUserId: 3);
      final req = t.adapter.requests.single;
      expect(req.method, 'POST');
      expect(req.path, '/api/v1/characters');
      final body = req.data as Map;
      expect(body['name'], '小爱');
      expect(body['bio'], '住在城南');
      expect(body['talkativeness'], 60);
      expect(body.keys.every((k) => const {
            'name', 'bio', 'personality', 'chat_style', 'greeting_message',
            'gender', 'appearance', 'talkativeness', 'talkativeness_locked',
            'timezone_offset',
          }.contains(k)), isTrue);
      expect(d.id, 7);
    });

    test('create 返回 owner 与当前账号不符 ⇒ ApiException（不当成功）', () async {
      final t = _make((_) => _json(_charJson, 201));
      final draft = CharacterDraft.blank()..setField('name', '小爱');
      expect(
        () => t.api.create(draft, expectedUserId: 99),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });

    test('create 本地校验失败 ⇒ client 错误且零请求', () async {
      final t = _make((_) => _json(_charJson, 201));
      final draft = CharacterDraft.blank()..setField('name', '   ');
      expect(
        () => t.api.create(draft),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'client')),
      );
      expect(t.adapter.requests, isEmpty);
    });

    test('update：PUT 目标 id，只提交改动字段，并核对 id/owner', () async {
      final t = _make((_) => _json({..._charJson, 'bio': '搬到城北'}));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', '搬到城北');
      final d = await t.api.update(7, draft, original);
      final req = t.adapter.requests.single;
      expect(req.method, 'PUT');
      expect(req.path, '/api/v1/characters/7');
      expect(req.data, {'bio': '搬到城北'});
      expect(d.bio, '搬到城北');
    });

    test('update 无实际变化 ⇒ 不发写请求，直接返回原对象', () async {
      final t = _make((_) => _json(_charJson));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', '住在城南');
      final d = await t.api.update(7, draft, original);
      expect(t.adapter.requests, isEmpty);
      expect(identical(d, original), isTrue);
    });

    test('update 目标 id 与原始对象不符 ⇒ client 错误且零请求', () async {
      final t = _make((_) => _json(_charJson));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', 'x');
      expect(
        () => t.api.update(8, draft, original),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'client')),
      );
      expect(t.adapter.requests, isEmpty);
    });

    test('update 返回 owner 变化 ⇒ ApiException（家庭角色也不能被换 owner）', () async {
      final t = _make((_) => _json({..._charJson, 'user_id': 4}));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', 'x');
      expect(
        () => t.api.update(7, draft, original),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });

    test('update 返回 id 变化 ⇒ ApiException', () async {
      final t = _make((_) => _json({..._charJson, 'id': 8}));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', 'x');
      expect(
        () => t.api.update(7, draft, original),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });

    test('404 归一为 ApiException(client) 且原始错误保留', () async {
      final t = _make((_) => _json({'detail': 'not found'}, 404));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', 'x');
      expect(
        () => t.api.update(7, draft, original),
        throwsA(isA<DioException>().having(
          (e) => (e.error is ApiException) ? (e.error as ApiException).statusCode : null,
          'status',
          404,
        )),
      );
    });
  });

  group('R1/R2 长期回归（主审反例长期化）', () {
    test('name 按 Unicode 码点计长：100 通过、101 拒绝（emoji）', () {
      final d = CharacterDraft.blank()
        ..setField('name', List.filled(100, '😀').join());
      expect(d.validate(), isNull);
      d.setField('name', List.filled(101, '😀').join());
      expect(d.validate(), 'name_too_long');
    });

    test('混合字符按码点计长（ASCII＋CJK＋emoji）', () {
      final hundred = 'a' * 30 + '中' * 30 + '😀' * 40; // 100 码点
      final d = CharacterDraft.blank()..setField('name', hundred);
      expect(d.validate(), isNull);
      d.setField('name', '$hundred🙂'); // 101 码点
      expect(d.validate(), 'name_too_long');
    });

    test('空白名（含全角空格/制表换行）一律不得通过', () {
      for (final blank in <String>['', '   ', '\u3000\u3000', '\t\n']) {
        final d = CharacterDraft.blank()..setField('name', blank);
        expect(d.validate(), 'name_required', reason: blank);
      }
    });

    test('★ 绕过 setField 注入非白名单键：create/update 负载都不得带出', () {
      final original = CharacterEditorData(id: 7, userId: 3, name: 'SYNTH');
      final d = original.toDraft();
      d.values['system_prompt'] = 'SYNTH_FORBIDDEN';
      d.touched.add('system_prompt');

      expect(d.toUpdatePayload(original).keys,
          everyElement(isIn(kCharacterEditorFields)));
      expect(d.toUpdatePayload(original).containsKey('system_prompt'), isFalse);
      expect(d.toCreatePayload().containsKey('system_prompt'), isFalse);
    });

    test('R2 修复不破坏已知字段的清空/省略语义', () {
      final data = CharacterEditorData.fromJson(_charJson);
      final cleared = data.toDraft()..setField('bio', '');
      expect(cleared.toUpdatePayload(data), {'bio': ''});
      final untouched = data.toDraft();
      expect(untouched.toUpdatePayload(data), isEmpty);
    });

    test('同一旧 original 的两次非空 update 会写两次（UI 必须自行上锁）', () async {
      final t = _make((_) => _json(_charJson));
      final original = CharacterEditorData.fromJson(_charJson);
      final draft = original.toDraft()..setField('bio', 'x');
      await t.api.update(7, draft, original);
      await t.api.update(7, draft, original);
      expect(t.adapter.requests.length, 2,
          reason: 'API 的 early-return 只处理「空差异」，不负责双击去重');
    });
  });

  group('幂等键（M2）', () {
    CharacterDraft neo() => CharacterDraft.blank()..setField('name', 'Neo');

    test('带 key + lang：两个头同时保留，且键只在创建 POST 上', () async {
      final t = _make((o) => _json(_charJson, 201));
      await t.api.load(7, lang: 'zh');
      await t.api.create(neo(), expectedUserId: 3, lang: 'zh', idempotencyKey: 'k-abc-123');
      final post = t.adapter.requests.last;
      expect(post.method, 'POST');
      expect(post.headers['Idempotency-Key'], 'k-abc-123');
      expect(post.headers['lang'], 'zh', reason: '加了键不能挤掉 lang');
      final body = post.data as Map;
      expect(body.containsKey('idempotency_key'), isFalse, reason: '键不得混进角色 payload');
      expect(body.containsKey('idempotencyKey'), isFalse);
      expect(t.adapter.requests.first.headers.containsKey('Idempotency-Key'), isFalse,
          reason: 'GET 不得带创建键');
    });

    test('无 key 的旧调用不带该头（向后兼容）', () async {
      final t = _make((o) => _json(_charJson, 201));
      await t.api.create(neo(), expectedUserId: 3);
      final post = t.adapter.requests.single;
      expect(post.headers.containsKey('Idempotency-Key'), isFalse);
      expect(post.headers['lang'], isNull);
    });

    test('空键原样发送（不 trim、不悄悄变成无键）', () async {
      final t = _make((o) => _json(_charJson, 201));
      await t.api.create(neo(), expectedUserId: 3, idempotencyKey: '  ');
      expect(t.adapter.requests.single.headers['Idempotency-Key'], '  ');
    });

    test('创建键不写进全局 dio.options（不污染其它请求）', () async {
      final t = _make((o) => _json(_charJson, 201));
      await t.api.create(neo(), expectedUserId: 3, idempotencyKey: 'k-global-guard');
      expect(t.client.dio.options.headers.containsKey('Idempotency-Key'), isFalse);
    });
  });
}
