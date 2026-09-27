import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/services/api_exception.dart';
import 'package:ai_companion/services/secure_token_store.dart';
import 'package:ai_companion/services/server_identity.dart';

/// 跨实现向量由 `backend/app/server_identity.py` 现算得到（批 0-3 M0-a 的同一份实现），
/// 目的：证明 Dart 与 Python 在 HKDF / 指纹 / 配对 mac / 响应签名上逐字节一致。
const _code = 'A2B3C4D5E6F7';
final _shared = base64Decode('PxBhqQY0Xam98VRu1pu+w8I4SyKIpYkwW7CnDnt28tM=');
const _fp = '39f4d5febf93';
const _challenge = 'abc123_XYZ-88~';
const _pairMac =
    '2c0faf95626bb3b7b31524ad7ed29d663f6cc119790aa186b21ac7707859909c';
const _bodyZh =
    r'{"status":"ok","timestamp":"2026-09-27T10:00:00.123456","zh":"中文"}';
const _proofZh =
    'v1 1790000000 074ff884a87513a47d19a23c3b93404847275c9e38ccc7fe7044fe551b152ca5';
const _body401 = '{"detail":"用户名或密码错误"}';
const _proof401NoNonce =
    'v1 1790000001 e15fd4594fbd315f3e44a6f69888b0e497c77dcabb53c39d1d40178ddfe4c88c';

class _MemStore implements SecureKeyValueStore {
  final Map<String, String> values = {};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;

  @override
  Future<void> delete(String key) async => values.remove(key);
}

ServerIdentity _identityWith({
  String? key,
  String baseUrl = 'http://192.168.1.10:8000',
  String mode = 'shadow',
}) {
  SharedPreferences.setMockInitialValues({
    if (key != null) ServerIdentity.prefKeyIdentity: key,
    if (key != null) ServerIdentity.prefKeyFingerprint: _fp,
    if (key != null) ServerIdentity.prefKeyBaseUrl: baseUrl,
    ServerIdentity.prefKeyVerifyMode: mode,
  });
  return ServerIdentity(secure: _MemStore());
}

Future<void> _expectGuard(ServerIdentity id, bool expected, String path) async {
  await id.ensureLoaded();
  expect(id.shouldGuard(path), expected, reason: path);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('派生原语与后端逐字节一致', () {
    test('HKDF-SHA256 派生 shared 与指纹', () {
      final derived = ServerIdentity.deriveShared(_code);
      expect(derived, _shared);
      expect(ServerIdentity.fingerprintOf(derived), _fp);
      expect(ServerIdentity.formatFingerprint(_fp), '39f4-d5fe-bf93');
    });

    test('配对码大小写与首尾空格不影响派生（后端同样 upper/strip）', () {
      expect(ServerIdentity.deriveShared('  a2b3c4d5e6f7 '), _shared);
    });

    test('配对挑战 mac = HMAC(shared, "v1\\n" + challenge)', () {
      expect(ServerIdentity.pairMac(_shared, _challenge), _pairMac);
    });

    test('响应签名覆盖 nonce/status/body/ts（含中文原文）', () {
      expect(ServerIdentity.proofMac(_shared, 'T0stNonce-abc', 200, _bodyZh, 1790000000),
          _proofZh.split(' ')[2]);
    });

    test('空 nonce 用占位符 "-" 参与摘要', () {
      expect(ServerIdentity.canonicalOf('', 401, utf8.encode(_body401), 1790000001),
          contains('-\n401\n'));
      expect(ServerIdentity.proofMac(_shared, '', 401, _body401, 1790000001),
          _proof401NoNonce.split(' ')[2]);
    });
  });

  group('验签判定', () {
    test('签名匹配 / 不匹配 / 缺头 / 格式非法', () async {
      final id = _identityWith(key: base64Encode(_shared));
      await id.ensureLoaded();
      expect(
          id.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: _bodyZh,
              proof: _proofZh),
          VerifyOutcome.ok);
      expect(
          id.verify(
              nonce: 'other-nonce',
              status: 200,
              bodyText: _bodyZh,
              proof: _proofZh),
          VerifyOutcome.mismatch);
      expect(
          id.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: '{"status":"tampered"}',
              proof: _proofZh),
          VerifyOutcome.mismatch);
      expect(
          id.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: _bodyZh,
              proof: null),
          VerifyOutcome.unsigned);
      expect(
          id.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: _bodyZh,
              proof: 'v2 abc'),
          VerifyOutcome.malformed);
      expect(id.verifiedCount, 1);
      expect(id.mismatchCount, 3); // 签名不符 2 次 + 格式非法 1 次（都算可疑）
      expect(id.unsignedCount, 1);
    });

    test('换了排版/键序的等价 JSON 摘要不同（必须拿原文验，不能重编码）', () {
      final reformatted = jsonEncode({
        'zh': '中文',
        'status': 'ok',
        'timestamp': '2026-09-27T10:00:00.123456',
      });
      expect(reformatted, isNot(_bodyZh));
      expect(
          ServerIdentity.proofMac(_shared, 'T0stNonce-abc', 200, reformatted, 1790000000),
          isNot(_proofZh.split(' ')[2]));
    });
  });

  group('未配对 / off 时零行为变化', () {
    test('未配对：任何路径都不加头不验签', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      await _expectGuard(_identityWith(), false, '/api/v1/system/health');
    });

    test('模式 off：已配对也不动作', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      await _expectGuard(
          _identityWith(key: base64Encode(_shared), mode: 'off'),
          false,
          '/api/v1/system/health');
    });

    test('已配对 + shadow：只有白名单路径受管', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      final id = _identityWith(key: base64Encode(_shared));
      await id.ensureLoaded();
      expect(id.isPaired, isTrue);
      await _expectGuard(id, true, '/api/v1/system/health');
      await _expectGuard(id, true, 'http://192.168.1.10:8000/api/v1/auth/login');
      await _expectGuard(id, false, '/api/v1/chat/send');
    });

    test('换服务器地址即视为未配对（必须重新配对）', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.99:8000';
      final id = _identityWith(key: base64Encode(_shared));
      expect(id.isPaired, isFalse);
      await _expectGuard(id, false, '/api/v1/system/health');
    });

    test('地址归一化忽略尾斜杠与大小写，端口参与判定', () async {
      final id = _identityWith(key: base64Encode(_shared));
      await id.ensureLoaded();
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000/';
      expect(id.isPaired, isTrue);
      ApiClientBaseUrl.current = 'HTTP://192.168.1.10:8000';
      expect(id.isPaired, isTrue);
      ApiClientBaseUrl.current = 'http://192.168.1.10:9000';
      expect(id.isPaired, isFalse);
    });

    test('密钥丢失/脏值视为未配对（不误拒，只当没配过）', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      final id = _identityWith(key: 'not-base64!!');
      await id.ensureLoaded();
      expect(id.isPaired, isFalse);
      expect(
          id.verify(nonce: 'n', status: 200, bodyText: '{}', proof: 'v1 1 aa'),
          VerifyOutcome.unsigned);
    });
  });

  group('配对码形态（12 位去易混字符）', () {
    test('合法/非法判定与后端字符表一致', () {
      expect(ServerIdentity.codeAlphabet.contains('0'), isFalse);
      expect(ServerIdentity.codeAlphabet.contains('O'), isFalse);
      expect(ServerIdentity.codeAlphabet.contains('I'), isFalse);
      expect(ServerIdentity.codeAlphabet.contains('L'), isFalse);
      expect(ServerIdentity.isPairingCodeValid('A2B3C4D5E6F7'), isTrue);
      expect(ServerIdentity.isPairingCodeValid('a2b3c4d5e6f7'), isTrue);
      expect(ServerIdentity.isPairingCodeValid('A2B3C4D5E6F'), isFalse);
      expect(ServerIdentity.isPairingCodeValid('A2B3C4D5E6F0'), isFalse);
      expect(ServerIdentity.isPairingCodeValid('A2B3C4D5E6F '), isFalse);
    });

    test('nonce 定长且不超后端 128 截断阈值', () {
      expect(ServerIdentity.newNonce().length, lessThanOrEqualTo(128));
      expect(ServerIdentity.newNonce() != ServerIdentity.newNonce(), isTrue);
    });

    test('非法配对码不发任何网络请求', () async {
      final id = _identityWith();
      final r = await id.pair('BAD', 'http://127.0.0.1:1/');
      expect(r.ok, isFalse);
      expect(r.failure, 'rejected');
    });
  });

  group('拦截器（真实 ApiClient 单例）', () {
    const signedBody = '{"status":"ok","zh":"中文"}';
    final captured = <String, dynamic>{};

    /// 服务器替身：记录请求头，并按需出签（mode 决定签不签）
    void stub({bool sign = false, String? overrideProof, String? badBody}) {
      ApiClient().dio.httpClientAdapter = _FakeAdapter((options) {
        captured['nonce'] = options.headers[ServerIdentity.challengeHeader];
        final body = badBody ?? signedBody;
        final ts = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
        final proof = overrideProof ??
            (sign
                ? 'v1 $ts ${ServerIdentity.proofMac(_shared, captured['nonce'] as String? ?? '', 200, body, ts)}'
                : null);
        return ResponseBody.fromString(body, 200, headers: {
          Headers.contentLengthHeader: [utf8.encode(body).length.toString()],
          Headers.contentTypeHeader: [Headers.jsonContentType],
          if (proof != null) ServerIdentity.proofHeader: [proof],
        });
      });
    }

    Future<void> pairedAs(String mode) async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      SharedPreferences.setMockInitialValues({
        ServerIdentity.prefKeyIdentity: base64Encode(_shared),
        ServerIdentity.prefKeyFingerprint: _fp,
        ServerIdentity.prefKeyBaseUrl: 'http://192.168.1.10:8000',
        ServerIdentity.prefKeyVerifyMode: mode,
      });
      ServerIdentity.instance.resetForTest();
      await ServerIdentity.instance.ensureLoaded();
    }

    test('未配对：不加 nonce 头、响应体照旧是 Map', () async {
      SharedPreferences.setMockInitialValues({});
      ServerIdentity.instance.resetForTest();
      await ServerIdentity.instance.ensureLoaded();
      stub();
      final r = await ApiClient().dio.get('/api/v1/system/health');
      expect(captured['nonce'], isNull);
      expect(r.data, isA<Map<String, dynamic>>());
      expect(r.data['zh'], '中文');
    });

    test('已配对 + shadow：带 nonce、验签通过、还原成 Map 交回调用方', () async {
      await pairedAs('shadow');
      final before = ServerIdentity.instance.verifiedCount;
      stub(sign: true);
      final r = await ApiClient().dio.get('/api/v1/system/health');
      expect(captured['nonce'], isA<String>());
      expect((captured['nonce'] as String).isNotEmpty, isTrue);
      expect(r.data, isA<Map<String, dynamic>>());
      expect(r.data['zh'], '中文');
      expect(ServerIdentity.instance.verifiedCount, before + 1);
    });

    test('服务器未出签（后端 off）：shadow 只记数，不拒绝', () async {
      await pairedAs('shadow');
      final before = ServerIdentity.instance.unsignedCount;
      stub();
      final r = await ApiClient().dio.get('/api/v1/system/health');
      expect(r.data['status'], 'ok');
      expect(ServerIdentity.instance.unsignedCount, before + 1);
    });

    test('已配对 + enforce：签名不符即拒绝，且语义不被二次归类', () async {
      await pairedAs('enforce');
      final ts = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
      // 用「另一条 nonce」出签：真服务器重放/换头的场景，必须判为不符
      stub(
          sign: true,
          overrideProof:
              'v1 $ts ${ServerIdentity.proofMac(_shared, 'other-request-nonce', 200, signedBody, ts)}');
      Object? caught;
      try {
        await ApiClient().dio.get('/api/v1/system/health');
        fail('enforce 下签名不符不应放行');
      } catch (e) {
        caught = e;
      }
      expect(caught, isA<DioException>());
      final inner = (caught as DioException).error;
      expect(inner, isA<ApiException>());
      expect((inner as ApiException).kind, 'identity');
    });

    test('白名单外的路径：不加头不验签（即便 enforce）', () async {
      await pairedAs('enforce');
      stub(sign: true, overrideProof: 'v1 1 ${'0' * 64}');
      final r = await ApiClient().dio.get('/api/v1/chat/send');
      expect(captured['nonce'], isNull);
      expect(r.data['status'], 'ok');
    });
  });
}

/// 只回固定 JSON 的假适配器（把请求头交给断言用）
class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.builder);

  final ResponseBody Function(RequestOptions options) builder;

  @override
  Future<ResponseBody> fetch(RequestOptions options,
      Stream<List<int>>? requestStream, Future<void>? cancelFuture) async =>
      builder(options);

  @override
  void close({bool force = false}) {}
}
