import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/auth/pair_server_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/services/api_exception.dart';
import 'package:ai_companion/services/secure_token_store.dart';
import 'package:ai_companion/services/server_identity.dart';

/// 跨实现向量由 `backend/app/server_identity.py` 现算得到（批 0-3 M0-a 的同一份实现），
/// 目的：证明 Dart 与 Python 在 HKDF / 密钥解包 / 配对 mac / 响应签名上逐字节一致。
///
/// 关键口径（派单 §0）：**验签密钥是 site（配对时从 wrapped_key 解出的全站身份密钥）**，
/// 配对码派生的 shared 只用于配对 mac。下面所有 proof 向量都用 site 出签。
const _code = 'A2B3C4D5E6F7';
final _shared = base64Decode('PxBhqQY0Xam98VRu1pu+w8I4SyKIpYkwW7CnDnt28tM=');
const _ksHex = 'b5223427fe95a39aabcde96d960dc07a14f3f8ec3eeb36e2f860be5beffe024c';
final _site = base64Decode('ERERERERERERERERERERERERERERERERERERERERERE='); // b"\x11" * 32
const _wrappedHex = 'a4332536ef84b28bbadcf87c871cd16b05e2e9fd2ffa27f3e971af4afeef135d';
const _siteFp = 'f37588d87ac0';
const _sharedFp = '39f4d5febf93'; // fingerprintOf(shared)：只作原语向量，不参与存储
const _challenge = 'abc123_XYZ-88~';
const _pairMac =
    '2c0faf95626bb3b7b31524ad7ed29d663f6cc119790aa186b21ac7707859909c';
const _bodyZh =
    r'{"status":"ok","timestamp":"2026-09-27T10:00:00.123456","zh":"中文"}';
const _proofZh =
    'v1 1790000000 8d7253f17002436753de2778539eead326d91e070aa34dbb8fe692ddf2e095ff';
const _body401 = '{"detail":"用户名或密码错误"}';
const _proof401NoNonce =
    'v1 1790000001 45812cab5d79684eaddd4258852d0b3adbecf5e714014434eab31e8a3049391d';

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
  String fp = _siteFp,
  String baseUrl = 'http://192.168.1.10:8000',
  String mode = 'shadow',
}) {
  SharedPreferences.setMockInitialValues({
    if (key != null) ServerIdentity.prefKeyIdentity: key,
    if (key != null) ServerIdentity.prefKeyFingerprint: fp,
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
    test('HKDF-SHA256 派生 shared 与配对 mac', () {
      expect(ServerIdentity.deriveShared(_code), _shared);
      expect(ServerIdentity.fingerprintOf(_shared), _sharedFp);
      expect(ServerIdentity.pairMac(_shared, _challenge), _pairMac);
    });

    test('HKDF 多块展开（L=42，RFC 5869 A.1 Case 1 输入）与独立实现同值', () {
      List<int> fromHex(String h) => List<int>.generate(
          h.length ~/ 2, (i) => int.parse(h.substring(i * 2, i * 2 + 2), radix: 16));
      final out = ServerIdentity.hkdfSha256(
        List<int>.filled(22, 0x0b),
        fromHex('000102030405060708090a0b0c'),
        fromHex('f0f1f2f3f4f5f6f7f8f9'),
        length: 42,
      );
      expect(out.length, 42);
      // 值由 Python `cryptography` 的 HKDF（与本仓库纯标准库实现相互独立）现算核对
      expect(
          out.map((b) => b.toRadixString(16).padLeft(2, '0')).join(),
          '3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf'
          '34007208d5b887185865');
    });

    test('包裹密钥流 ks 与 pair-mac 密钥互不挪用（后端同标签口径）', () {
      final ks = ServerIdentity.deriveKeystream(_code);
      expect(ks.map((b) => b.toRadixString(16).padLeft(2, '0')).join(), _ksHex);
      expect(ks, isNot(_shared));
    });

    test('配对码大小写与首尾空格不影响派生（后端同样 upper/strip）', () {
      expect(ServerIdentity.deriveShared('  a2b3c4d5e6f7 '), _shared);
      expect(ServerIdentity.deriveKeystream('  a2b3c4d5e6f7 '),
          ServerIdentity.deriveKeystream(_code));
    });

    test('指纹 = HMAC(site, "ambrace-fp-v1")[:6]，显示成 4-4-4', () {
      expect(ServerIdentity.fingerprintOf(_site), _siteFp);
      expect(ServerIdentity.formatFingerprint(_siteFp), 'f375-88d8-7ac0');
    });

    test('响应签名覆盖 nonce/status/body/ts（含中文原文，密钥是 site）', () {
      expect(
          ServerIdentity.proofMac(
              _site, 'T0stNonce-abc', 200, _bodyZh, 1790000000),
          _proofZh.split(' ')[2]);
    });

    test('空 nonce 用占位符 "-" 参与摘要', () {
      expect(ServerIdentity.canonicalOf('', 401, utf8.encode(_body401), 1790000001),
          contains('-\n401\n'));
      expect(
          ServerIdentity.proofMac(
              _site, '', 401, _body401, 1790000001),
          _proof401NoNonce.split(' ')[2]);
    });
  });

  group('wrapped_key 解包 → 身份密钥', () {
    test('site = wrapped XOR ks（与后端下发值同值）', () {
      expect(ServerIdentity.unwrapIdentity(_wrappedHex, ServerIdentity.deriveKeystream(_code)),
          _site);
    });

    test('非法十六进制 / 长度不符 / 奇数位一律返回 null', () {
      final ks = ServerIdentity.deriveKeystream(_code);
      expect(ServerIdentity.unwrapIdentity('', ks), isNull);
      expect(ServerIdentity.unwrapIdentity('zz' * 32, ks), isNull);
      expect(ServerIdentity.unwrapIdentity(_wrappedHex.substring(0, 62), ks), isNull);
      expect(ServerIdentity.unwrapIdentity(_wrappedHex.substring(0, 63), ks), isNull);
    });

    test('大写十六进制与首尾空格照样解出（后端下发为小写，这里只做容错）', () {
      expect(
          ServerIdentity.unwrapIdentity(
              ' ${_wrappedHex.toUpperCase()} ', ServerIdentity.deriveKeystream(_code)),
          _site);
    });
  });

  group('验签判定', () {
    test('签名匹配 / 不匹配 / 缺头 / 格式非法', () async {
      final id = _identityWith(key: base64Encode(_site));
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

    test('拿配对码派生的 shared 去验必须判不符（验签密钥只能是 site）', () async {
      expect(
          ServerIdentity.proofMac(
              _shared, 'T0stNonce-abc', 200, _bodyZh, 1790000000),
          isNot(_proofZh.split(' ')[2]));
      final id = _identityWith(key: base64Encode(_shared));
      await id.ensureLoaded();
      expect(
          id.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: _bodyZh,
              proof: _proofZh),
          VerifyOutcome.mismatch);
    });

    test('换了排版/键序的等价 JSON 摘要不同（必须拿原文验，不能重编码）', () {
      final reformatted = jsonEncode({
        'zh': '中文',
        'status': 'ok',
        'timestamp': '2026-09-27T10:00:00.123456',
      });
      expect(reformatted, isNot(_bodyZh));
      expect(
          ServerIdentity.proofMac(
              _site, 'T0stNonce-abc', 200, reformatted, 1790000000),
          isNot(_proofZh.split(' ')[2]));
    });
  });

  group('配对流程（配对码不出网，落盘的是 site）', () {
    const pairChallenge = 'test-challenge-abc';
    const pairMacForTest =
        '0a67da49cbc23eb9ebb3a93a8245d2047dcc0d4d54b058636eb7b46067c12a81';

    /// 服务器替身：pair-start / pair 两段应答可控，并记录真正发出的请求体
    _FakeServer fakeServer({
      String? wrappedKey,
      String? fp,
      int? failStatus,
    }) {
      return _FakeServer((req, sent) {
        if (req.data != null) sent[req.path] = jsonEncode(req.data);
        ResponseBody json(Object body, int status) =>
            ResponseBody.fromString(jsonEncode(body), status, headers: {
              Headers.contentTypeHeader: [Headers.jsonContentType]
            });
        if (req.path.endsWith('pair-start')) {
          if (failStatus == 409) {
            return json({'detail': 'no active pairing code'}, 409);
          }
          return json(
              {'challenge': pairChallenge, 'server_name': 'AMBRACE Server'}, 200);
        }
        if (failStatus == 401) return json({'detail': 'pairing failed'}, 401);
        return json({
          'status': 'ok',
          'server_name': 'AMBRACE Server',
          'fp': fp ?? _siteFp,
          'fp_display': ServerIdentity.formatFingerprint(fp ?? _siteFp),
          if (wrappedKey != null) 'wrapped_key': _wrappedHex,
        }, 200);
      });
    }

    test('成功配对：解包出 site 并落盘（安全存储 + prefs 副本），配对码不上网', () async {
      SharedPreferences.setMockInitialValues({});
      final server = fakeServer(wrappedKey: _wrappedHex);
      final r = await server.id.pair(_code, 'http://192.168.1.10:8000');
      expect(r.ok, isTrue);
      expect(r.fingerprint, _siteFp);
      expect(r.fingerprintDisplay, 'f375-88d8-7ac0');
      // 网络上只跑 challenge/mac：配对码本身绝不出现在任何请求体里
      final wire = server.sent.values.join();
      expect(wire, contains(pairMacForTest));
      expect(wire, isNot(contains(_code)));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(ServerIdentity.prefKeyIdentity), base64Encode(_site));
      expect(prefs.getString(ServerIdentity.prefKeyFingerprint), _siteFp);
      expect(prefs.getString(ServerIdentity.prefKeyBaseUrl),
          'http://192.168.1.10:8000');
      // 落盘的密钥就是验签用的密钥：解出的 site 能验过服务器用 site 出的签
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      final reloaded = _identityWith(key: base64Encode(_site));
      await reloaded.ensureLoaded();
      expect(
          reloaded.verify(
              nonce: 'T0stNonce-abc',
              status: 200,
              bodyText: _bodyZh,
              proof: _proofZh),
          VerifyOutcome.ok);
    });

    test('服务器不下发 wrapped_key（旧版本）⇒ unsupported', () async {
      SharedPreferences.setMockInitialValues({});
      final server = fakeServer();
      final r = await server.id.pair(_code, 'http://192.168.1.10:8000');
      expect(r.failure, 'unsupported');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(ServerIdentity.prefKeyIdentity), isNull,
          reason: '解不出密钥绝不能落盘');
    });

    test('回带指纹与本地解出的密钥不一致 ⇒ 拒绝并落空（不写密钥）', () async {
      SharedPreferences.setMockInitialValues({});
      final server = fakeServer(wrappedKey: _wrappedHex, fp: _sharedFp);
      final r = await server.id.pair(_code, 'http://192.168.1.10:8000');
      expect(r.ok, isFalse);
      expect(r.failure, 'fp_mismatch');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(ServerIdentity.prefKeyIdentity), isNull);
    });

    test('409（无待用码）/ 401（码或应答不符）分别给出可区分的失败原因', () async {
      SharedPreferences.setMockInitialValues({});
      final noCode = fakeServer(failStatus: 409);
      expect((await noCode.id.pair(_code, 'http://127.0.0.1:8000')).failure,
          'no_code');
      final rejected = fakeServer(failStatus: 401);
      expect((await rejected.id.pair(_code, 'http://127.0.0.1:8000')).failure,
          'rejected');
    });

    test('非法配对码不发任何网络请求', () async {
      final server = fakeServer(wrappedKey: _wrappedHex);
      final r = await server.id.pair('BAD', 'http://127.0.0.1:1/');
      expect(r.ok, isFalse);
      expect(r.failure, 'rejected');
      expect(server.sent, isEmpty);
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
          _identityWith(key: base64Encode(_site), mode: 'off'),
          false,
          '/api/v1/system/health');
    });

    test('已配对 + shadow：只有白名单路径受管', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      final id = _identityWith(key: base64Encode(_site));
      await id.ensureLoaded();
      expect(id.isPaired, isTrue);
      await _expectGuard(id, true, '/api/v1/system/health');
      await _expectGuard(id, true, 'http://192.168.1.10:8000/api/v1/auth/login');
      await _expectGuard(id, false, '/api/v1/chat/send');
    });

    test('换服务器地址即视为未配对（必须重新配对）', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.99:8000';
      final id = _identityWith(key: base64Encode(_site));
      expect(id.isPaired, isFalse);
      await _expectGuard(id, false, '/api/v1/system/health');
    });

    test('地址归一化忽略尾斜杠与大小写，端口参与判定', () async {
      final id = _identityWith(key: base64Encode(_site));
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

  group('三态保护状态与文案（不许制造全绿错觉）', () {
    test('未配对 / off=未受保护 / shadow=部分受保护 / enforce 全过=受保护', () async {
      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      expect(_identityWith().protectionState, ProtectionState.unpaired);
      final off = _identityWith(key: base64Encode(_site), mode: 'off');
      await off.ensureLoaded();
      expect(off.protectionState, ProtectionState.unprotected);
      final shadow = _identityWith(key: base64Encode(_site));
      await shadow.ensureLoaded();
      expect(shadow.protectionState, ProtectionState.partial);
      final enforce =
          _identityWith(key: base64Encode(_site), mode: 'enforce');
      await enforce.ensureLoaded();
      // enforce 但一次都没验过 ⇒ 仍不能报「受保护」
      expect(enforce.protectionState, ProtectionState.partial);
      enforce.verify(
          nonce: 'T0stNonce-abc', status: 200, bodyText: _bodyZh, proof: _proofZh);
      expect(enforce.protectionState, ProtectionState.guarded);
      enforce.verify(
          nonce: 'x', status: 200, bodyText: _bodyZh, proof: null);
      expect(enforce.protectionState, ProtectionState.partial,
          reason: '出现未签名即降级为部分受保护');
    });

    testWidgets('配对页状态卡按三态出文案', (tester) async {
      Widget wrap(Widget child) => MaterialApp(
            localizationsDelegates: AppLocalizations.localizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            locale: const Locale('zh'),
            home: child,
          );

      ApiClientBaseUrl.current = 'http://192.168.1.10:8000';
      SharedPreferences.setMockInitialValues({
        ServerIdentity.prefKeyIdentity: base64Encode(_site),
        ServerIdentity.prefKeyFingerprint: _siteFp,
        ServerIdentity.prefKeyBaseUrl: 'http://192.168.1.10:8000',
        ServerIdentity.prefKeyVerifyMode: 'shadow',
      });
      ServerIdentity.instance.resetForTest();
      await ServerIdentity.instance.ensureLoaded();
      await tester.pumpWidget(wrap(const PairServerScreen()));
      expect(find.textContaining('部分受保护（指纹 f375-88d8-7ac0）'), findsOneWidget);

      await tester.tap(find.text('只记录'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('拒绝可疑响应'));
      await tester.pumpAndSettle();
      expect(find.text('受保护（指纹 f375-88d8-7ac0）：白名单端点逐条验签通过'),
          findsNothing); // 一次都没验过时仍是「部分受保护」
      ServerIdentity.instance.verify(
          nonce: 'T0stNonce-abc', status: 200, bodyText: _bodyZh, proof: _proofZh);
      // 计数器变化不会自己触发 setState：用非 const 实例强制重建一次
      await tester.pumpWidget(wrap(PairServerScreen()));
      expect(find.textContaining('受保护（指纹 f375-88d8-7ac0）：白名单端点逐条验签通过'),
          findsOneWidget);

      SharedPreferences.setMockInitialValues({});
      ServerIdentity.instance.resetForTest();
      await ServerIdentity.instance.ensureLoaded();
      await tester.pumpWidget(wrap(const PairServerScreen()));
      expect(find.textContaining('未配对'), findsOneWidget);
    });
  });

  group('配对码形态与 nonce（与后端字符表一致）', () {
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

    test('验签白名单与后端 SIGN_PATHS 同集合（读后端常量对齐，不各写一套）', () {
      final src = File('../backend/app/server_identity.py').readAsStringSync();
      final block = RegExp(r'SIGN_PATHS = frozenset\(\{(.*?)\}\)', dotAll: true)
          .firstMatch(src)!
          .group(1)!;
      final backend = RegExp(r'"(/api/[^"]+)"')
          .allMatches(block)
          .map((m) => m.group(1)!)
          .toSet();
      expect(backend, isNotEmpty);
      expect(ServerIdentity.signPaths, backend);
    });
  });

  group('拦截器（真实 ApiClient 单例）', () {
    const signedBody = '{"status":"ok","zh":"中文"}';
    final captured = <String, dynamic>{};

    /// 服务器替身：记录请求头，并按需出签（密钥是 site）
    void stub({bool sign = false, String? overrideProof, String? badBody}) {
      ApiClient().dio.httpClientAdapter = _FakeAdapter((options) {
        captured['nonce'] = options.headers[ServerIdentity.challengeHeader];
        final body = badBody ?? signedBody;
        final ts = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
        final proof = overrideProof ??
            (sign
                ? 'v1 $ts ${ServerIdentity.proofMac(_site, captured['nonce'] as String? ?? '', 200, body, ts)}'
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
        ServerIdentity.prefKeyIdentity: base64Encode(_site),
        ServerIdentity.prefKeyFingerprint: _siteFp,
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
              'v1 $ts ${ServerIdentity.proofMac(_site, 'other-request-nonce', 200, signedBody, ts)}');
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

/// 配对流程用的服务器替身：自带一个 [ServerIdentity]（内存密钥存储 + 假适配器），
/// 并把每个带请求体的请求原样记进 [sent]，供「配对码不出网」这类断言使用。
class _FakeServer {
  _FakeServer(this._handler);

  final ResponseBody Function(RequestOptions req, Map<String, String> sent)
      _handler;

  final Map<String, String> sent = {};

  late final ServerIdentity id = ServerIdentity(
    secure: _MemStore(),
    newDio: (options) => Dio(options)
      ..httpClientAdapter = _FakeAdapter((req) => _handler(req, sent)),
  );
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
