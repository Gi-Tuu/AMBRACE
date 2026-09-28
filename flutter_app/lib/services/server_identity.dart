import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'secure_token_store.dart';

/// 服务器身份固定（批 0-3 M0-b，2026-09-27）——App 侧配对、密钥/指纹存储与响应验签。
///
/// 跨实现契约必须与 `backend/app/server_identity.py` 逐字节一致（派单以该实现为准）：
///
///     shared  = HKDF-SHA256(ikm=code, salt="ambrace-id-v1", info="ambrace-pair-v1", L=32)
///     ks      = HKDF-SHA256(ikm=code, salt="ambrace-id-v1", info="ambrace-ks-v1",  L=32)
///     site    = wrapped_key XOR ks          # 身份密钥（全站单份），配对时以密钥流包裹后下发
///     fp      = HMAC-SHA256(site, "ambrace-fp-v1")[:6].hex()  → 12 位十六进制，4-4-4 展示
///     pairMac = HMAC-SHA256(shared, "v1\n" + challenge).hex()
///     proof   = "v1 {ts} " + HMAC-SHA256(site, "\n".join([nonce, status,
///                sha256(body).hex(), ts])).hex()
///
/// **验签用的密钥是 site，不是 code 派生的 shared**：身份密钥全站单份，签发新配对码、
/// 第二台设备配对都不更换它（后端方案 §6-R5）。因此配对成功必须解包 wrapped_key 拿到
/// site，绝不能拿 shared 去验签——shared 只用于配对 mac，拿它验签会条条不符。
///
/// 三条口径同后端：配对码绝不过网（只本地派生）；只防伪造不防窃听；
/// **未配对 / 校验模式 off 时本文件不改变任何既有请求与响应处理**。

/// 验签结果（[ServerIdentity.verify]）。
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

/// 保护状态三档（App「服务器身份」入口显示用，方案 §4.5：**不许制造全绿错觉**）。
enum ProtectionState {
  /// 未配对（本机没有该服务器的身份密钥，或该地址换过服务器需要重新配对）
  unpaired,

  /// 部分受保护：已配对，但白名单外的通道（图片/音频直链、下载直链、SSE 分块、
  /// WebSocket 帧、原生通道）不做签名校验；验签模式非 enforce，或本轮出现过未签名/不符，
  /// 也归这一档（宁少报不多报）
  partial,

  /// 未受保护：已配对但验签模式为 off（默认档），请求照发照收、不做任何校验
  unprotected,

  /// 受保护：enforce 档，且本轮受管请求全部验签通过（仍只是「白名单内」受保护）
  guarded,
}

/// 配对结果（[ServerIdentity.pair] 不抛异常，统一返回值，便于 UI 走 l10n 文案）。
class PairResult {
  final bool ok;
  final String serverName;
  final String fingerprint;

  /// 失败原因（`no_code` / `rejected` / `network` / `unsupported` / `fp_mismatch`），成功时为 null
  final String? failure;

  const PairResult.success(this.serverName, this.fingerprint)
      : ok = true,
        failure = null;

  const PairResult.failed(this.failure)
      : ok = false,
        serverName = '',
        fingerprint = '';

  /// 指纹展示形式：12 位十六进制 → 4-4-4
  String get fingerprintDisplay => ServerIdentity.formatFingerprint(fingerprint);
}

/// 服务器身份（每 App 安装一份，绑定配对时的服务器地址）。
class ServerIdentity {
  ServerIdentity({SecureKeyValueStore? secure, Dio Function(BaseOptions)? newDio})
      : _secure = secure ?? const FlutterSecureKeyValueStore(),
        _newDio = newDio ?? _defaultDio;

  static Dio _defaultDio(BaseOptions options) => Dio(options);

  static final ServerIdentity instance = ServerIdentity();

  // ── 与后端 server_identity.py 对齐的形态常量 ──────────────────────────────
  static const String challengeHeader = 'x-ambrace-challenge';
  static const String proofHeader = 'x-ambrace-proof';
  static const int codeLength = 12;
  static const String codeAlphabet = '23456789ABCDEFGHJKMNPQRSTUVWXYZ';

  static const List<int> _hkdfSalt = [
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x69, 0x64, 0x2d, 0x76, 0x31 // ambrace-id-v1
  ];
  static const List<int> _hkdfInfo = [
    // ambrace-pair-v1
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x70, 0x61, 0x69, 0x72, 0x2d, 0x76, 0x31
  ];
  static const List<int> _hkdfKsInfo = [
    // ambrace-ks-v1（包裹身份密钥用的密钥流派生标签，与 pair-mac 密钥分开）
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x6b, 0x73, 0x2d, 0x76, 0x31
  ];
  static const List<int> _fpLabel = [
    // ambrace-fp-v1
    0x61, 0x6d, 0x62, 0x72, 0x61, 0x63, 0x65, 0x2d, 0x66, 0x70, 0x2d, 0x76, 0x31
  ];

  /// 响应签名白名单（必须与后端 `SIGN_PATHS` 同集合）
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

  // ── 存储 key（SharedPreferences 落盘后会带 `flutter.` 前缀，Kotlin 侧同口径）──
  static const String prefKeyIdentity = 'server_identity_key';
  static const String prefKeyFingerprint = 'server_identity_fp';
  static const String prefKeyBaseUrl = 'server_identity_base_url';
  static const String prefKeyVerifyMode = 'server_identity_verify_mode';

  static const Duration _secureTimeout = Duration(seconds: 3);

  final SecureKeyValueStore _secure;
  final Dio Function(BaseOptions) _newDio;

  Uint8List? _site;
  String _pairedBaseUrl = '';
  String _fingerprint = '';
  String _mode = 'off';
  bool _loaded = false;
  Future<void>? _loading;

  /// 验签统计（UI 展示「部分受保护」用；不落盘、重启归零）
  int verifiedCount = 0;
  int mismatchCount = 0;
  int unsignedCount = 0;

  bool get isLoaded => _loaded;

  /// 当前地址是否已完成配对（换地址即视为未配对，必须重新配对）。
  bool get isPaired {
    final key = _site;
    if (key == null || key.isEmpty) return false;
    if (_pairedBaseUrl.isEmpty) return false;
    return _pairedBaseUrl == normalizeBaseUrl(ApiClientBaseUrl.current);
  }

  String get fingerprint => _fingerprint;
  String get fingerprintDisplay => formatFingerprint(_fingerprint);

  /// `off`（不验签）/ `shadow`（验签但只记录）/ `enforce`（验签失败拒绝响应）。
  String get verifyMode => _mode;

  set verifyMode(String value) {
    _mode = const {'off', 'shadow', 'enforce'}.contains(value) ? value : 'off';
  }

  /// 入口显示的保护状态（方案 §4.5：白名单外的通道永不声称已覆盖，故最高只到「受保护」
  /// 而非「全部受保护」；只要不是 enforce 或本轮出现过未签名/不符，一律报「部分受保护」）。
  ProtectionState get protectionState {
    if (!isPaired) return ProtectionState.unpaired;
    if (_mode == 'off') return ProtectionState.unprotected;
    if (_mode == 'enforce' && mismatchCount == 0 && unsignedCount == 0 &&
        verifiedCount > 0) {
      return ProtectionState.guarded;
    }
    return ProtectionState.partial;
  }

  static String formatFingerprint(String fp) {
    if (fp.isEmpty) return '';
    final parts = <String>[];
    for (var i = 0; i < fp.length; i += 4) {
      parts.add(fp.substring(i, i + 4 > fp.length ? fp.length : i + 4));
    }
    return parts.join('-');
  }

  /// 归一化服务器地址（仅 scheme+host+port 参与配对归属判定，忽略尾部斜杠与路径）
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

  /// 配对码合法性：长度 12、字符集去易混（0/O/1/I/L 不在表内），大小写均可
  static bool isPairingCodeValid(String code) {
    if (code.length != codeLength) return false;
    return !code.toUpperCase().split('').any((c) => !codeAlphabet.contains(c));
  }

  // ── 加载 / 落盘 ────────────────────────────────────────────────────────────

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

  /// 后台触发一次加载（调用方不等待、不抛异常）：读不到就下次再试，
  /// 期间一律按「未配对」处理，保证既有请求零变化。
  void kickLoad() {
    if (_loaded || _loading != null) return;
    ensureLoaded().catchError((Object _) {});
  }

  /// 测试专用：清空进程内状态（与后端 `reset_state_for_test` 对称；生产路径不调用）
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
    final prefs = await SharedPreferences.getInstance();
    verifyMode = prefs.getString(prefKeyVerifyMode) ?? 'off';
    _pairedBaseUrl = prefs.getString(prefKeyBaseUrl) ?? '';
    _fingerprint = prefs.getString(prefKeyFingerprint) ?? '';
    _site = await _readStoredKey(prefs);
  }

  Future<Uint8List?> _readStoredKey(SharedPreferences prefs) async {
    if (SecureTokenStore.platformSupportsSecure) {
      try {
        final raw = await _secure.read(prefKeyIdentity).timeout(_secureTimeout);
        final decoded = _decodeKey(raw);
        if (decoded != null) return decoded;
      } catch (_) {
        // 安全存储不可用：回退明文 prefs（待遇降级但不打断既有流程，同 SecureTokenStore 口径）
      }
    }
    return _decodeKey(prefs.getString(prefKeyIdentity));
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

  /// 配对成功后落盘（安全存储 + 明文 prefs 副本供 Kotlin 通道读取）
  Future<void> _persist(Uint8List site, String fp, String baseUrl) async {
    final b64 = base64Encode(site);
    final prefs = await SharedPreferences.getInstance();
    if (SecureTokenStore.platformSupportsSecure) {
      try {
        await _secure.write(prefKeyIdentity, b64).timeout(_secureTimeout);
      } catch (_) {
        // 忽略：下面仍写 prefs 副本，密钥不至于丢失
      }
    }
    await prefs.setString(prefKeyIdentity, b64);
    await prefs.setString(prefKeyFingerprint, fp);
    await prefs.setString(prefKeyBaseUrl, normalizeBaseUrl(baseUrl));
    _site = site;
    _fingerprint = fp;
    _pairedBaseUrl = normalizeBaseUrl(baseUrl);
  }

  /// 解除配对（清密钥与指纹；`server_identity_verify_mode` 保留用户选择）
  Future<void> unpair() async {
    final prefs = await SharedPreferences.getInstance();
    if (SecureTokenStore.platformSupportsSecure) {
      try {
        await _secure.delete(prefKeyIdentity).timeout(_secureTimeout);
      } catch (_) {
        // 忽略：下面仍清 prefs
      }
    }
    await prefs.remove(prefKeyIdentity);
    await prefs.remove(prefKeyFingerprint);
    await prefs.remove(prefKeyBaseUrl);
    _site = null;
    _fingerprint = '';
    _pairedBaseUrl = '';
  }

  Future<void> saveVerifyMode(String mode) async {
    verifyMode = mode;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(prefKeyVerifyMode, _mode);
  }

  // ── 派生原语 ───────────────────────────────────────────────────────────────

  static Uint8List _hmac(List<int> key, List<int> data) =>
      Uint8List.fromList(Hmac(sha256, key).convert(data).bytes);

  /// RFC 5869 HKDF-SHA256（extract + expand），与后端 `hkdf_sha256` 同值
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

  /// 包裹用密钥流（后端 `derive_keystream`：与 pair-mac 密钥不同标签，互不挪用）
  static Uint8List deriveKeystream(String code) => hkdfSha256(
      ascii.encode(code.trim().toUpperCase()), _hkdfSalt, _hkdfKsInfo);

  /// 身份密钥还原：site = wrapped_key XOR ks（与后端 `unwrap_identity` 同法）。
  /// 十六进制长度/字符合法性不符时返回 null（旧服务器不下发 wrapped_key 等异常形态）。
  static Uint8List? unwrapIdentity(String wrappedHex, Uint8List keystream) {
    final hex = wrappedHex.trim().toLowerCase();
    if (hex.length != keystream.length * 2) return null;
    // 解析失败先哨兵 -1 判掉：不能塞进 Uint8List（-1 会截断成 255，脏值就混过去了）
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

  /// 配对用挑战应答 mac（`"v1\n" + challenge`）
  static String pairMac(Uint8List shared, String challenge) =>
      _hmac(shared, utf8.encode('v1\n$challenge')).map(_hex2).join();

  /// 响应签名原文（与后端 `proof_for` 的 canonical 完全一致）
  static String canonicalOf(String nonce, int status, List<int> body, int ts) =>
      [nonce.isEmpty ? '-' : nonce, '$status', _sha256Hex(body), '$ts'].join('\n');

  /// 随机 nonce（响应签名的防重放原料；后端不校验、不因缺失而拒绝）
  static String newNonce() {
    final rnd = Random.secure();
    final bytes = Uint8List.fromList(List<int>.generate(16, (_) => rnd.nextInt(256)));
    return base64Url.encode(bytes).replaceAll('=', '');
  }

  // ── 验签入口 ───────────────────────────────────────────────────────────────

  /// 该路径本轮是否需要「带 nonce + 验签」：未配对或模式 off 时永远 false（零行为变化）
  bool shouldGuard(String path) {
    if (!_loaded || _mode == 'off' || !isPaired) return false;
    return signPaths.contains(Uri.tryParse(path)?.path ?? path);
  }

  /// 响应签名 mac（与后端 `proof_for` 的第三段同值）；[bodyText] 必须是原文，
  /// [identityKey] 是配对时解包得到的 site（**不是**配对码派生的 shared）
  static String proofMac(Uint8List identityKey, String nonce, int status,
          String bodyText, int ts) =>
      _hmac(identityKey,
              utf8.encode(canonicalOf(nonce, status, utf8.encode(bodyText), ts)))
          .map(_hex2)
          .join();

  /// 校验一条响应签名。[bodyText] 必须是服务器返回的**原文**（UTF-8 解码后逐字符），
  /// 不可用重新编码的 JSON（键序/转义差异会改变摘要）。
  VerifyOutcome verify({
    required String nonce,
    required int status,
    required String bodyText,
    required String? proof,
  }) {
    final site = _site;
    if (site == null || site.isEmpty) return VerifyOutcome.unsigned;
    final header = (proof ?? '').trim();
    if (header.isEmpty) {
      unsignedCount++;
      return VerifyOutcome.unsigned;
    }
    final parts = header.split(' ');
    if (parts.length != 3 || parts[0] != 'v1') {
      mismatchCount++;
      return VerifyOutcome.malformed;
    }
    final ts = int.tryParse(parts[1]);
    if (ts == null) {
      mismatchCount++;
      return VerifyOutcome.malformed;
    }
    final expect = proofMac(site, nonce, status, bodyText, ts);
    // 长度不等直接判负（hex 定长，比对本身也不泄露密钥）
    if (expect.length != parts[2].length ||
        expect.toLowerCase() != parts[2].toLowerCase()) {
      mismatchCount++;
      return VerifyOutcome.mismatch;
    }
    verifiedCount++;
    return VerifyOutcome.ok;
  }

  /// 时间戳偏差（秒）：只做提示，不参与判定（nonce 已把签名绑定到本次请求）
  static int proofSkewSeconds(String proof) {
    final parts = proof.trim().split(' ');
    if (parts.length != 3) return 0;
    final ts = int.tryParse(parts[1]);
    if (ts == null) return 0;
    return (DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000 - ts).abs();
  }

  // ── 配对流程（配对码只在本机派生，网络上只跑 challenge/response）────────────

  /// 用桌面控制台显示的 12 位配对码与 [baseUrl] 对应的服务器完成一次配对。
  ///
  /// 网络上只跑 challenge / mac / wrapped_key：**配对码不出网**，身份密钥以码派生密钥流
  /// 包裹后下发，本机解包得到 site 并用它做后续验签（方案 §4.3 / §6-R5）。
  Future<PairResult> pair(String code, String baseUrl) async {
    if (!isPairingCodeValid(code)) return const PairResult.failed('rejected');
    final url = baseUrl.trim();
    if (url.isEmpty) return const PairResult.failed('network');
    final shared = deriveShared(code);
    final dio = _newDio(BaseOptions(
        baseUrl: url.replaceAll(RegExp(r'/+$'), ''),
        connectTimeout: const Duration(seconds: 5),
        receiveTimeout: const Duration(seconds: 10)));
    try {
      final start = await dio.post('/api/v1/system/identity/pair-start');
      final challenge = (start.data is Map)
          ? (start.data as Map)['challenge'] as String?
          : null;
      if (challenge == null || challenge.isEmpty) {
        return const PairResult.failed('no_code');
      }
      final done = await dio.post('/api/v1/system/identity/pair', data: {
        'challenge': challenge,
        'mac': pairMac(shared, challenge),
      });
      final data = done.data is Map ? done.data as Map : const {};
      final site = unwrapIdentity(
          (data['wrapped_key'] as String?) ?? '', deriveKeystream(code));
      if (site == null) return const PairResult.failed('unsupported');
      final fp = (data['fp'] as String?) ?? '';
      // 双源自洽：本地解包出的密钥算出的指纹必须等于服务器回带的 fp，
      // 不等说明下发内容被换过（或码/密钥流派生错位），此时绝不能落盘
      if (fp.isEmpty || fingerprintOf(site) != fp) {
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
}

/// `ServerIdentity.isPaired` 需要知道「当前服务器地址」，但 [ApiClient] 与本文件互相引用，
/// 故把这一格信息收在极小的持有类里，由 [ApiClient.configure] / [ApiClient.updateBaseUrl] 写入。
class ApiClientBaseUrl {
  ApiClientBaseUrl._();

  static String current = '';
}
