import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

/// 桩适配器：把请求交给测试给定的处理器，不发起真实网络。
class _StubAdapter implements HttpClientAdapter {
  _StubAdapter(this.handler);
  final Future<ResponseBody> Function(RequestOptions) handler;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream,
          Future<void>? cancelFuture) =>
      handler(options);

  @override
  void close({bool force = false}) {}
}

Dio _dioWith(Future<ResponseBody> Function(RequestOptions) h) {
  final dio = Dio(BaseOptions(baseUrl: 'http://127.0.0.1:18080'));
  dio.httpClientAdapter = _StubAdapter(h);
  return dio;
}

Future<AmbIdentityVerifier> _pairedVerifier({String mode = 'enforce'}) async {
  final store = InMemoryKeyValueStore();
  final site = Uint8List.fromList(List<int>.generate(32, (i) => i * 7 % 256));
  await store.write(AmbIdentityVerifier.prefKeyIdentity, base64Encode(site));
  await store.write(AmbIdentityVerifier.prefKeyBaseUrl,
      AmbIdentity.normalizeBaseUrl('http://127.0.0.1:18080'));
  await store.write(AmbIdentityVerifier.prefKeyVerifyMode, mode);
  final v = AmbIdentityVerifier(store: store, currentBaseUrl: 'http://127.0.0.1:18080');
  await v.ensureLoaded();
  return v;
}

void main() {
  group('身份协议原语（与后端同法，确定性）', () {
    test('HKDF 派生确定 + unwrap 还原 + 指纹 12 位十六进制', () {
      final ks = AmbIdentity.deriveKeystream('23456789ABCD');
      expect(ks.length, 32);
      expect(AmbIdentity.deriveKeystream('23456789ABCD'), ks); // 确定性
      final site = Uint8List.fromList(List<int>.generate(32, (i) => (i * 3 + 1) % 256));
      final wrapped = List<int>.generate(32, (i) => site[i] ^ ks[i]);
      final hex = wrapped.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
      expect(AmbIdentity.unwrapIdentity(hex, ks), site);
      final fp = AmbIdentity.fingerprintOf(site);
      expect(fp.length, 12);
      expect(RegExp(r'^[0-9a-f]{12}$').hasMatch(fp), isTrue);
      expect(AmbIdentity.formatFingerprint('abcdef123456'), 'abcd-ef12-3456');
    });

    test('配对码合法性 + 地址归一', () {
      expect(AmbIdentity.isPairingCodeValid('23456789ABCD'), isTrue);
      expect(AmbIdentity.isPairingCodeValid('23456789ABC0'), isFalse); // 含 0
      expect(AmbIdentity.isPairingCodeValid('short'), isFalse);
      expect(AmbIdentity.normalizeBaseUrl('HTTP://127.0.0.1:18080/'), 'http://127.0.0.1:18080');
    });

    test('verify 四态：ok / unsigned / mismatch / malformed', () {
      final site = Uint8List.fromList(List<int>.generate(32, (i) => i));
      const body = '{"a":1}';
      final ts = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
      final mac = AmbIdentity.proofMac(site, 'n1', 200, body, ts);
      expect(
          AmbIdentity.verifyWith(site: site, nonce: 'n1', status: 200, bodyText: body, proof: 'v1 $ts $mac'),
          VerifyOutcome.ok);
      expect(
          AmbIdentity.verifyWith(site: site, nonce: 'n1', status: 200, bodyText: body, proof: null),
          VerifyOutcome.unsigned);
      expect(
          AmbIdentity.verifyWith(site: site, nonce: 'n1', status: 200, bodyText: body, proof: 'v1 $ts ${'0' * 64}'),
          VerifyOutcome.mismatch);
      expect(
          AmbIdentity.verifyWith(site: site, nonce: 'n1', status: 200, bodyText: body, proof: 'oops'),
          VerifyOutcome.malformed);
      // 未配对：一律 unsigned（零行为变化）
      expect(
          AmbIdentity.verifyWith(site: null, nonce: 'n', status: 200, bodyText: body, proof: 'v1 1 x'),
          VerifyOutcome.unsigned);
    });
  });

  group('本地化映射（返工组 ⑤：英文界面不得出现中文错误原文）', () {
    test('错误 kind → 按语言映射文案', () {
      const zh = CoreStrings('zh');
      const en = CoreStrings('en');
      expect(zh.errorForKind('unauthorized'), '登录已失效，请重新登录');
      expect(en.errorForKind('unauthorized'), 'Session expired, please sign in again');
      expect(en.errorForKind('network'), en.t('offline'));
      expect(en.errorForKind('identity'), en.t('identityMismatch'));
      expect(en.errorForKind(null), en.t('errorUnknown'));
      // en 下不得回落到中文硬编码
      for (final kind in ['unauthorized', 'network', 'timeout', 'server', 'client',
        'cancelled', 'identity', null]) {
        expect(RegExp(r'[\u4e00-\u9fff]').hasMatch(en.errorForKind(kind)), isFalse,
            reason: 'en 文案含中文：$kind');
      }
    });

    test('配对失败码 → 本地化，且不回显内部码', () {
      const en = CoreStrings('en');
      expect(en.pairFailureText('network'), en.t('errorNetwork'));
      expect(en.pairFailureText('rejected'), en.t('errorClient'));
      expect(en.pairFailureText('fp_mismatch'), en.t('identityMismatch'));
      // 「不回显内部码」的精确判据：返回值必须是**已映射的本地化文案之一**，
      // 且绝不等于原因码本身（不能靠 substring 判——'rejected' 恰好是英文文案里的正常词）。
      final allowed = {
        en.t('errorNetwork'), en.t('errorClient'),
        en.t('identityMismatch'), en.t('errorUnknown'),
      };
      for (final code in ['no_code', 'rejected', 'unsupported', 'fp_mismatch', 'network']) {
        final out = en.pairFailureText(code);
        expect(out, isNot(equals(code)), reason: '原因码被原样回显：$code');
        expect(allowed.contains(out), isTrue, reason: '未走本地化映射：$code → $out');
      }
    });

    test('localizedError：DioException→ApiException→按 kind 本地化', () {
      const en = CoreStrings('en');
      final err = DioException(
        requestOptions: RequestOptions(path: '/x'),
        type: DioExceptionType.badResponse,
        response: Response<dynamic>(
            requestOptions: RequestOptions(path: '/x'), statusCode: 401),
      );
      expect(localizedError(err, en), en.t('unauthorized'));
      expect(localizedError(const ApiException(kind: 'identity', message: '中文原文'), en),
          en.t('identityMismatch'));
      expect(localizedError(null, en), en.t('errorUnknown'));
    });
  });

  group('API 客户端：身份验签与错误归一（验收 1/2：不能伪成功）', () {
    test('未配对时受管路径零行为变化（不加 challenge、不改 plain）', () async {
      String? seenChallenge;
      ResponseType? seenType;
      final dio = _dioWith((o) async {
        seenChallenge = o.headers['x-ambrace-challenge'] as String?;
        seenType = o.responseType;
        return ResponseBody.fromString('{"ok":true}', 200,
            headers: {Headers.contentTypeHeader: ['application/json']});
      });
      final client = AmbApiClient(dio: dio); // 默认 verifier 未配对
      client.configure(baseUrl: 'http://127.0.0.1:18080');
      final r = await client.dio.post('/api/v1/auth/login', data: {});
      expect(r.data, {'ok': true});
      expect(seenChallenge, isNull);
      expect(seenType, ResponseType.json);
    });

    test('enforce 档：签名不符 ⇒ 拒绝响应（kind=identity），不伪成功', () async {
      final v = await _pairedVerifier();
      final dio = _dioWith((o) async => ResponseBody.fromString('{"ok":true}', 200,
          headers: {AmbIdentity.proofHeader: ['v1 1 ${'0' * 64}']}));
      final client = AmbApiClient(identity: v, dio: dio);
      client.configure(baseUrl: 'http://127.0.0.1:18080');
      await expectLater(
        client.dio.post('/api/v1/auth/login', data: {}),
        throwsA(isA<DioException>().having(
            (e) => (e.error as ApiException).kind, 'kind', 'identity')),
      );
      expect(v.mismatchCount, greaterThan(0));
    });

    test('enforce 档：签名正确 ⇒ 通过并解码 JSON（用的是服务器原文）', () async {
      final v = await _pairedVerifier();
      final site = Uint8List.fromList(List<int>.generate(32, (i) => i * 7 % 256));
      const body = '{"access_token":"tok-123"}';
      final dio = _dioWith((o) async {
        final nonce = o.headers[AmbIdentity.challengeHeader] as String? ?? '';
        final ts = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
        final mac = AmbIdentity.proofMac(site, nonce, 200, body, ts);
        return ResponseBody.fromString(body, 200,
            headers: {AmbIdentity.proofHeader: ['v1 $ts $mac']});
      });
      final client = AmbApiClient(identity: v, dio: dio);
      client.configure(baseUrl: 'http://127.0.0.1:18080');
      final token = await ChatTransport(client).login('u', 'p');
      expect(token, 'tok-123');
      expect(v.verifiedCount, greaterThan(0));
    });

    test('401 归一为 unauthorized 并触发 onUnauthorized 钩子（带 token、非认证端点）', () async {
      final dio = _dioWith((o) async => ResponseBody.fromString('{"detail":"expired"}', 401,
          headers: {Headers.contentTypeHeader: ['application/json']}));
      final client = AmbApiClient(dio: dio);
      var fired = 0;
      client.onUnauthorized = () => fired++;
      client.configure(baseUrl: 'http://127.0.0.1:18080', token: 't');
      await expectLater(
        client.dio.get('/api/v1/characters'),
        throwsA(isA<DioException>().having(
            (e) => (e.error as ApiException).kind, 'kind', 'unauthorized')),
      );
      await Future<void>.delayed(Duration.zero);
      expect(fired, 1);
    });

    test('登录端点 401 不触发会话失效钩子（认证端点豁免）', () async {
      final dio = _dioWith((o) async => ResponseBody.fromString('{"detail":"bad"}', 401,
          headers: {Headers.contentTypeHeader: ['application/json']}));
      final client = AmbApiClient(dio: dio);
      var fired = 0;
      client.onUnauthorized = () => fired++;
      client.configure(baseUrl: 'http://127.0.0.1:18080', token: 't');
      await expectLater(client.dio.post('/api/v1/auth/login', data: {}), throwsA(anything));
      await Future<void>.delayed(Duration.zero);
      expect(fired, 0);
    });

    test('角色列表 / 会话历史解析（共享模型）', () async {
      final dio = _dioWith((o) async {
        const json = {Headers.contentTypeHeader: ['application/json']};
        if (o.path.contains('/characters')) {
          return ResponseBody.fromString(
              '{"characters":[{"id":1,"name":"A"},{"id":2,"name":"B"}]}', 200, headers: json);
        }
        return ResponseBody.fromString(
            '{"messages":[{"id":1,"session_id":9,"sender_type":"user","content":"hi","created_at":"2026-10-15 09:00:00"}]}',
            200, headers: json);
      });
      final client = AmbApiClient(dio: dio);
      client.configure(baseUrl: 'http://127.0.0.1:18080');
      final t = ChatTransport(client);
      final chars = await t.getCharacters();
      expect(chars.map((c) => c.name).toList(), ['A', 'B']);
      final msgs = await t.getMessages(9);
      expect(msgs.single.content, 'hi');
      expect(msgs.single.isUser, isTrue);
    });
  });
}
