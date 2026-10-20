import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart' as core;
// 本文件自身也要**用**这两个枚举（`export` 只对导入方生效，不引入本地作用域），故显式 import。
import 'package:ambrace_core/ambrace_core.dart' show VerifyOutcome, ProtectionState;
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'secure_token_store.dart';

// 两个枚举转发 core 的权威定义（类型名与取值不变，旧引用/断言照常工作）。
export 'package:ambrace_core/ambrace_core.dart' show VerifyOutcome, ProtectionState;

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

/// 验签结果 / 保护状态：见文件顶部 `export`（转发 core 的权威定义，本文件不再自带一份）。
///
/// `VerifyOutcome`：ok / unsigned / mismatch / malformed；
/// `ProtectionState`：unpaired / partial / unprotected / guarded（**不许制造全绿错觉**）。

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

  // 协议常量（盐/标签）与派生算法**已移入 core**（`AmbIdentity`），本文件不再自带副本；
  // 下方所有 `_hkdfSalt` 之类的旧私有常量随实现一并删除，避免出现第二份真相。

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

  static String formatFingerprint(String fp) => core.AmbIdentity.formatFingerprint(fp);

  /// 归一化服务器地址（**委托 core**：归属判定口径只有一份）。
  static String normalizeBaseUrl(String url) => core.AmbIdentity.normalizeBaseUrl(url);

  /// 配对码合法性（**委托 core**：长度 12、字符集去易混）。
  static bool isPairingCodeValid(String code) => core.AmbIdentity.isPairingCodeValid(code);

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

  // ── 派生原语（**全部委托 core**：协议只有一份权威实现，见主审 §1）────────────

  /// RFC 5869 HKDF-SHA256。
  static Uint8List hkdfSha256(List<int> ikm, List<int> salt, List<int> info,
          {int length = 32}) =>
      core.AmbIdentity.hkdfSha256(ikm, salt, info, length: length);

  static Uint8List deriveShared(String code) => core.AmbIdentity.deriveShared(code);

  static Uint8List deriveKeystream(String code) => core.AmbIdentity.deriveKeystream(code);

  /// 身份密钥还原：site = wrapped_key XOR ks。
  static Uint8List? unwrapIdentity(String wrappedHex, Uint8List keystream) =>
      core.AmbIdentity.unwrapIdentity(wrappedHex, keystream);

  static String fingerprintOf(Uint8List key) => core.AmbIdentity.fingerprintOf(key);

  /// 配对用挑战应答 mac（`"v1\n" + challenge`）。
  static String pairMac(Uint8List shared, String challenge) =>
      core.AmbIdentity.pairMac(shared, challenge);

  /// 响应签名原文（与后端 `proof_for` 的 canonical 完全一致）。
  static String canonicalOf(String nonce, int status, List<int> body, int ts) =>
      core.AmbIdentity.canonicalOf(nonce, status, body, ts);

  /// 随机 nonce（响应签名的防重放原料）。
  static String newNonce() => core.AmbIdentity.newNonce();

  /// 响应签名 mac（第三段同值）。
  static String proofMac(Uint8List identityKey, String nonce, int status,
          String bodyText, int ts) =>
      core.AmbIdentity.proofMac(identityKey, nonce, status, bodyText, ts);

  // ── 验签入口 ───────────────────────────────────────────────────────────────

  /// 该路径本轮是否需要「带 nonce + 验签」：未配对或模式 off 时永远 false（零行为变化）
  bool shouldGuard(String path) {
    if (!_loaded || _mode == 'off' || !isPaired) return false;
    return signPaths.contains(Uri.tryParse(path)?.path ?? path);
  }

  /// 校验一条响应签名（**委托 core**：判定只有一份实现）。
  VerifyOutcome verify({
    required String nonce,
    required int status,
    required String bodyText,
    required String? proof,
  }) {
    final outcome = core.AmbIdentity.verifyWith(
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

  /// 时间戳偏差（秒）：只做提示，不参与判定。
  static int proofSkewSeconds(String proof) => core.AmbIdentity.proofSkewSeconds(proof);

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
