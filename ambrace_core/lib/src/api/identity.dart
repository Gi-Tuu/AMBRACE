import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';

import '../platform/key_value_store.dart';

/// 验签结果。
enum VerifyOutcome {
  /// 签名匹配
  ok,

  /// 已配对且该路径应签名，但响应没带 `X-Ambrace-Proof`（服务器身份模式为 off 时属正常）
  unsigned,

  /// 有签名但 HMAC 不符（伪造 / 中间人 / 密钥已轮换）
  mismatch,

  /// 签名头格式无法解析
  malformed,
}

/// 保护状态三档（UI 显示用：**不许制造全绿错觉**）。
enum ProtectionState { unpaired, partial, unprotected, guarded }

/// 配对结果（[AmbIdentityVerifier.pair] 不抛异常，统一返回值）。
class PairResult {
  final bool ok;
  final String serverName;
  final String fingerprint;
  final String? failure; // no_code / rejected / network / unsupported / fp_mismatch

  const PairResult.success(this.serverName, this.fingerprint)
      : ok = true,
        failure = null;

  const PairResult.failed(this.failure)
      : ok = false,
        serverName = '',
        fingerprint = '';

  String get fingerprintDisplay => AmbIdentity.formatFingerprint(fingerprint);
}

/// 服务器身份**协议原语**（与后端 `backend/app/server_identity.py` 逐字节一致）。
///
/// 这是跨端唯一权威实现：手机壳与 Web 壳都调用同一份（不再各自复制一套 HKDF/HMAC）。
class AmbIdentity {
  AmbIdentity._();

  static const String challengeHeader = 'x-ambrace-challenge';
  static const String proofHeader = 'x-ambrace-proof';
  static const int codeLength = 12;
  static const String codeAlphabet = '23456789ABCDEFGHJKMNPQRSTUVWXYZ';

  static const List<int> _hkdfSalt = [
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x69, 0x64, 0x2d, 0x76, 0x31
  ];
  static const List<int> _hkdfInfo = [
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x70, 0x61, 0x69, 0x72, 0x2d, 0x76, 0x31
  ];
  static const List<int> _hkdfKsInfo = [
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x6b, 0x73, 0x2d, 0x76, 0x31
  ];
  static const List<int> _fpLabel = [
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x66, 0x70, 0x2d, 0x76, 0x31
  ];

  /// 响应签名白名单（必须与后端 `SIGN_PATHS` 同集合）。
  static const Set<String> signPaths = {
    '/api/v1/system/health',
    '/api/v1/system/ready',
    '/api/v1/system/status',
    '/api/v1/system/liveness',
    '/api/v1/system/updates',
    '/api/v1/auth/login',
    '/api/v1/system/identity/pair-start',
    '/api/v1/system/identity/pair',
  };

  static Uint8List _hmac(List<int> key, List<int> data) =>
      Uint8List.fromList(Hmac(sha256, key).convert(data).bytes);

  /// RFC 5869 HKDF-SHA256（extract + expand）。
  static Uint8List hkdfSha256(List<int> ikm, List<int> salt, List<int> info,
      {int length = 32}) {
    final prk = _hmac(salt, ikm);
    final okm = BytesBuilder(copy: false);
    var block = const <int>[];
    var counter = 1;
    while (okm.length < length) {
      final input = BytesBuilder(copy: false)
        ..add(block)
        ..add(info)
        ..addByte(counter);
      block = _hmac(prk, input.takeBytes());
      okm.add(block);
      counter++;
    }
    return Uint8List.sublistView(okm.takeBytes(), 0, length);
  }

  static Uint8List deriveShared(String code) => hkdfSha256(
      ascii.encode(code.trim().toUpperCase()), _hkdfSalt, _hkdfInfo);

  static Uint8List deriveKeystream(String code) => hkdfSha256(
      ascii.encode(code.trim().toUpperCase()), _hkdfSalt, _hkdfKsInfo);

  /// 身份密钥还原：site = wrapped_key XOR ks。
  static Uint8List? unwrapIdentity(String wrappedHex, Uint8List keystream) {
    final hex = wrappedHex.trim().toLowerCase();
    if (hex.length != keystream.length * 2) return null;
    final raw = List<int>.generate(keystream.length,
        (i) => int.tryParse(hex.substring(i * 2, i * 2 + 2), radix: 16) ?? -1);
    if (raw.any((b) => b < 0 || b > 255)) return null;
    return Uint8List.fromList(
        List<int>.generate(keystream.length, (i) => raw[i] ^ keystream[i]));
  }

  static String fingerprintOf(Uint8List key) =>
      _hmac(key, _fpLabel).sublist(0, 6).map(_hex2).join();

  static String _hex2(int b) => b.toRadixString(16).padLeft(2, '0');

  static String _sha256Hex(List<int> body) => sha256.convert(body).toString();

  static String pairMac(Uint8List shared, String challenge) =>
      _hmac(shared, utf8.encode('v1\n$challenge')).map(_hex2).join();

  static String canonicalOf(String nonce, int status, List<int> body, int ts) =>
      [nonce.isEmpty ? '-' : nonce, '$status', _sha256Hex(body), '$ts'].join('\n');

  static String newNonce() {
    final rnd = Random.secure();
    final bytes = Uint8List.fromList(List<int>.generate(16, (_) => rnd.nextInt(256)));
    return base64Url.encode(bytes).replaceAll('=', '');
  }

  /// 响应签名 mac（第三段同值）；[identityKey] 是配对解包得到的 site（**不是** shared）。
  static String proofMac(Uint8List identityKey, String nonce, int status,
          String bodyText, int ts) =>
      _hmac(identityKey,
              utf8.encode(canonicalOf(nonce, status, utf8.encode(bodyText), ts)))
          .map(_hex2)
          .join();

  static String formatFingerprint(String fp) {
    if (fp.isEmpty) return '';
    final parts = <String>[];
    for (var i = 0; i < fp.length; i += 4) {
      parts.add(fp.substring(i, i + 4 > fp.length ? fp.length : i + 4));
    }
    return parts.join('-');
  }

  /// 归一化服务器地址（仅 scheme+host+port 参与配对归属判定）。
  static String normalizeBaseUrl(String url) {
    final trimmed = url.trim();
    if (trimmed.isEmpty) return '';
    final uri = Uri.tryParse(trimmed);
    if (uri == null || !uri.hasAuthority) {
      return trimmed.replaceAll(RegExp(r'/+$'), '').toLowerCase();
    }
    final port = uri.hasPort ? ':${uri.port}' : '';
    return '${uri.scheme.toLowerCase()}://${uri.host.toLowerCase()}$port';
  }

  /// 配对码合法性：长度 12、字符集去易混（0/O/1/I/L 不在表内），大小写均可。
  static bool isPairingCodeValid(String code) {
    if (code.length != codeLength) return false;
    return !code.toUpperCase().split('').any((c) => !codeAlphabet.contains(c));
  }

  /// 时间戳偏差（秒）：只做提示，不参与判定。
  static int proofSkewSeconds(String proof) {
    final parts = proof.trim().split(' ');
    if (parts.length != 3) return 0;
    final ts = int.tryParse(parts[1]);
    if (ts == null) return 0;
    return (DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000 - ts).abs();
  }

  /// 校验一条响应签名。[bodyText] 必须是服务器返回的**原文**（UTF-8 解码后逐字符）。
  static VerifyOutcome verifyWith({
    required Uint8List? site,
    required String nonce,
    required int status,
    required String bodyText,
    required String? proof,
    void Function()? onUnsigned,
    void Function()? onMismatch,
  }) {
    if (site == null || site.isEmpty) return VerifyOutcome.unsigned;
    final header = (proof ?? '').trim();
    if (header.isEmpty) {
      onUnsigned?.call();
      return VerifyOutcome.unsigned;
    }
    final parts = header.split(' ');
    if (parts.length != 3 || parts[0] != 'v1') {
      onMismatch?.call();
      return VerifyOutcome.malformed;
    }
    final ts = int.tryParse(parts[1]);
    if (ts == null) {
      onMismatch?.call();
      return VerifyOutcome.malformed;
    }
    final expect = proofMac(site, nonce, status, bodyText, ts);
    if (expect.length != parts[2].length ||
        expect.toLowerCase() != parts[2].toLowerCase()) {
      onMismatch?.call();
      return VerifyOutcome.mismatch;
    }
    return VerifyOutcome.ok;
  }
}

/// 身份状态持有者（壳注入存储；纯 Dart，无原生依赖）。
class AmbIdentityVerifier {
  AmbIdentityVerifier({
    KeyValueStore? store,
    this.currentBaseUrl = '',
    Dio Function(BaseOptions)? newDio,
  })  : _store = store ?? InMemoryKeyValueStore(),
        _newDio = newDio ?? ((o) => Dio(o));

  static const String prefKeyIdentity = 'server_identity_key';
  static const String prefKeyFingerprint = 'server_identity_fp';
  static const String prefKeyBaseUrl = 'server_identity_base_url';
  static const String prefKeyVerifyMode = 'server_identity_verify_mode';

  final KeyValueStore _store;
  final Dio Function(BaseOptions) _newDio;

  /// 当前服务器地址（壳在 configure 时写入，用于「换地址即未配对」判定）。
  String currentBaseUrl;

  Uint8List? _site;
  String _pairedBaseUrl = '';
  String _fingerprint = '';
  String _mode = 'off';
  bool _loaded = false;
  Future<void>? _loading;

  int verifiedCount = 0;
  int mismatchCount = 0;
  int unsignedCount = 0;

  bool get isLoaded => _loaded;
  bool get isPaired =>
      _site != null &&
      _site!.isNotEmpty &&
      _pairedBaseUrl.isNotEmpty &&
      _pairedBaseUrl == AmbIdentity.normalizeBaseUrl(currentBaseUrl);
  String get fingerprint => _fingerprint;
  String get verifyMode => _mode;

  set verifyMode(String value) {
    _mode = const {'off', 'shadow', 'enforce'}.contains(value) ? value : 'off';
  }

  ProtectionState get protectionState {
    if (!isPaired) return ProtectionState.unpaired;
    if (_mode == 'off') return ProtectionState.unprotected;
    if (_mode == 'enforce' &&
        mismatchCount == 0 &&
        unsignedCount == 0 &&
        verifiedCount > 0) {
      return ProtectionState.guarded;
    }
    return ProtectionState.partial;
  }

  Future<void> ensureLoaded() async {
    if (_loaded) return;
    final pending = _loading;
    if (pending != null) return pending;
    final future = _load();
    _loading = future;
    try {
      await future;
      _loaded = true;
    } finally {
      _loading = null;
    }
  }

  void kickLoad() {
    if (_loaded || _loading != null) return;
    ensureLoaded().catchError((Object _) {});
  }

  void resetForTest() {
    _site = null;
    _pairedBaseUrl = '';
    _fingerprint = '';
    _mode = 'off';
    _loaded = false;
    _loading = null;
    verifiedCount = 0;
    mismatchCount = 0;
    unsignedCount = 0;
  }

  Future<void> _load() async {
    verifyMode = await _store.read(prefKeyVerifyMode) ?? 'off';
    _pairedBaseUrl = await _store.read(prefKeyBaseUrl) ?? '';
    _fingerprint = await _store.read(prefKeyFingerprint) ?? '';
    _site = _decodeKey(await _store.read(prefKeyIdentity));
  }

  static Uint8List? _decodeKey(String? raw) {
    final s = raw?.trim();
    if (s == null || s.isEmpty) return null;
    try {
      final bytes = base64Decode(s);
      return bytes.length == 32 ? Uint8List.fromList(bytes) : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> unpair() async {
    await _store.delete(prefKeyIdentity);
    await _store.delete(prefKeyFingerprint);
    await _store.delete(prefKeyBaseUrl);
    _site = null;
    _fingerprint = '';
    _pairedBaseUrl = '';
  }

  Future<void> saveVerifyMode(String mode) async {
    verifyMode = mode;
    await _store.write(prefKeyVerifyMode, _mode);
  }

  bool shouldGuard(String path) {
    if (!_loaded || _mode == 'off' || !isPaired) return false;
    return AmbIdentity.signPaths.contains(Uri.tryParse(path)?.path ?? path);
  }

  VerifyOutcome verify({
    required String nonce,
    required int status,
    required String bodyText,
    required String? proof,
  }) {
    final outcome = AmbIdentity.verifyWith(
      site: _site,
      nonce: nonce,
      status: status,
      bodyText: bodyText,
      proof: proof,
      onUnsigned: () => unsignedCount++,
      onMismatch: () => mismatchCount++,
    );
    if (outcome == VerifyOutcome.ok) verifiedCount++;
    return outcome;
  }

  /// 真实配对（配对码只在本机派生；网络上只跑 challenge/mac/wrapped_key）。
  Future<PairResult> pair(String code, String baseUrl) async {
    if (!AmbIdentity.isPairingCodeValid(code)) return const PairResult.failed('rejected');
    final url = baseUrl.trim();
    if (url.isEmpty) return const PairResult.failed('network');
    final shared = AmbIdentity.deriveShared(code);
    final dio = _newDio(BaseOptions(
        baseUrl: url.replaceAll(RegExp(r'/+$'), ''),
        connectTimeout: const Duration(seconds: 5),
        receiveTimeout: const Duration(seconds: 10)));
    try {
      final start = await dio.post('/api/v1/system/identity/pair-start');
      final challenge =
          (start.data is Map) ? (start.data as Map)['challenge'] as String? : null;
      if (challenge == null || challenge.isEmpty) {
        return const PairResult.failed('no_code');
      }
      final done = await dio.post('/api/v1/system/identity/pair', data: {
        'challenge': challenge,
        'mac': AmbIdentity.pairMac(shared, challenge),
      });
      final data = done.data is Map ? done.data as Map : const {};
      final site = AmbIdentity.unwrapIdentity(
          (data['wrapped_key'] as String?) ?? '', AmbIdentity.deriveKeystream(code));
      if (site == null) return const PairResult.failed('unsupported');
      final fp = (data['fp'] as String?) ?? '';
      if (fp.isEmpty || AmbIdentity.fingerprintOf(site) != fp) {
        return const PairResult.failed('fp_mismatch');
      }
      await _persist(site, fp, url);
      return PairResult.success((data['server_name'] as String?) ?? '', fp);
    } on DioException catch (e) {
      final code409 = e.response?.statusCode;
      if (code409 == 409) return const PairResult.failed('no_code');
      if (code409 == 401) return const PairResult.failed('rejected');
      return PairResult.failed(e.type == DioExceptionType.connectionTimeout ||
              e.type == DioExceptionType.receiveTimeout ||
              e.type == DioExceptionType.connectionError
          ? 'network'
          : 'rejected');
    } catch (_) {
      return const PairResult.failed('rejected');
    } finally {
      dio.close(force: true);
    }
  }

  Future<void> _persist(Uint8List site, String fp, String baseUrl) async {
    await _store.write(prefKeyIdentity, base64Encode(site));
    await _store.write(prefKeyFingerprint, fp);
    await _store.write(prefKeyBaseUrl, AmbIdentity.normalizeBaseUrl(baseUrl));
    _site = site;
    _fingerprint = fp;
    _pairedBaseUrl = AmbIdentity.normalizeBaseUrl(baseUrl);
  }
}
