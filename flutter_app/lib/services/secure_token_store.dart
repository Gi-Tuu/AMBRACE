import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 安全存储读写的最小抽象。
///
/// 生产实现是 [FlutterSecureKeyValueStore]（Android=Keystore 加密存储，
/// iOS=Keychain）。测试注入内存替身即可覆盖迁移与降级分支，无需依赖平台通道，
/// 因此也能稳定模拟「安全存储抛异常」的降级场景。
abstract class SecureKeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

/// [SecureKeyValueStore] 的 flutter_secure_storage 实现。
class FlutterSecureKeyValueStore implements SecureKeyValueStore {
  const FlutterSecureKeyValueStore();

  /// flutter_secure_storage 11.x：Android 默认即 Keystore 方案
  /// （AES-GCM 数据加密 + RSA-OAEP 密钥包装，API 23+），v9 的
  /// `encryptedSharedPreferences` 开关已被移除，无需再传。
  /// iOS 取 first_unlock：设备重启后用户首次解锁起，后台服务即可读到 token。
  static const FlutterSecureStorage _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(),
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
  );

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

/// P2-A（2026-09-11 全量审查）：JWT 会话凭据安全存储。
///
/// * Android 走 Keystore 加密存储、iOS 走 Keychain，避免 root/越狱设备或
///   adb 备份直接拿到明文会话凭据（迁移前是明文 shared_prefs / NSUserDefaults）。
/// * **一次性迁移**：安全存储尚无 token、而 SharedPreferences 中还残留旧版明文
///   [tokenKey] 时，写入安全存储并在「读回校验通过」后删除明文；幂等，可重复调用。
/// * **降级**：Web/桌面（[platformSupportsSecure] 为 false），或安全存储调用抛
///   异常（如 Keystore 暂不可用）时，回退 SharedPreferences；读/写/删均不抛出，
///   保证登录与登出流程绝不因安全存储失败而崩溃或把用户登出。
/// * **存活上限**：每次安全存储调用都有 [defaultOpTimeout] 超时（见该常量说明），
///   平台通道无响应时降级而不是无限等待，避免启动加载/登录被拖死。
class SecureTokenStore {
  /// [secure] / [supportsSecure] / [opTimeout] 仅供测试注入；生产请用 [SecureTokenStore.instance]。
  SecureTokenStore({
    SecureKeyValueStore? secure,
    bool? supportsSecure,
    Duration? opTimeout,
  })  : _secure = secure ?? const FlutterSecureKeyValueStore(),
        _supportsSecure = supportsSecure ?? platformSupportsSecure,
        _opTimeout = opTimeout ?? defaultOpTimeout;

  /// 生产路径的全局单例。
  static final SecureTokenStore instance = SecureTokenStore();

  /// 键名与迁移前的明文键保持一致，便于无损迁移。
  static const String tokenKey = 'auth_token';

  /// 单次安全存储调用的默认存活上限。
  ///
  /// Keystore/Keychain 正常在数十毫秒内返回；但平台通道一旦不可用（插件未注册、
  /// Keystore 卡死、宿主环境没有实现等），其 Future 可能**永不完成**——若无限等待，
  /// 启动时的 load() 与登录时的 setAuth() 会一起挂起（表现为登录转圈不停）。
  /// 超时即按「安全存储不可用」降级到 SharedPreferences，保证流程始终可用。
  static const Duration defaultOpTimeout = Duration(seconds: 3);

  final SecureKeyValueStore _secure;
  final bool _supportsSecure;
  final Duration _opTimeout;

  /// 仅 Android/iOS 有 Keystore/Keychain 语义；Web 与桌面一律降级。
  static bool get platformSupportsSecure =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);

  /// 当前实例是否走安全存储（false=全程降级 SharedPreferences）。
  bool get supportsSecure => _supportsSecure;

  /// 读取 token；顺带完成旧明文 → 安全存储的一次性迁移。
  Future<String> readToken() async {
    final prefs = await SharedPreferences.getInstance();
    if (!_supportsSecure) {
      return prefs.getString(tokenKey) ?? '';
    }
    try {
      final stored = await _secure.read(tokenKey).timeout(_opTimeout);
      if (stored != null && stored.isNotEmpty) {
        // 安全存储已有权威值：顺手清掉可能残留的旧明文（迁移被中断等）。
        if (prefs.containsKey(tokenKey)) {
          await prefs.remove(tokenKey);
        }
        return stored;
      }
      // 安全存储为空 → 升级场景：把旧版明文搬进安全存储。
      final legacy = prefs.getString(tokenKey);
      if (legacy == null || legacy.isEmpty) {
        return '';
      }
      await _secure.write(tokenKey, legacy).timeout(_opTimeout);
      if (await _secure.read(tokenKey).timeout(_opTimeout) == legacy) {
        // 只有读回校验成功才删明文；失败则保留明文，下次启动重试迁移。
        await prefs.remove(tokenKey);
      }
      return legacy;
    } catch (_) {
      // Keystore/Keychain 异常：回退旧位，绝不因此把用户登出。
      return prefs.getString(tokenKey) ?? '';
    }
  }

  /// 写入 token：安全存储优先，并删除 SharedPreferences 里的旧明文。
  Future<void> writeToken(String token) async {
    final prefs = await SharedPreferences.getInstance();
    if (_supportsSecure) {
      try {
        await _secure.write(tokenKey, token).timeout(_opTimeout);
        await prefs.remove(tokenKey);
        return;
      } catch (_) {
        // 落到下方降级分支：至少保证本次登录可持久化，不阻塞登录流程。
      }
    }
    await prefs.setString(tokenKey, token);
  }

  /// 删除 token（登出）：两边都清；任一侧失败都不抛出。
  Future<void> deleteToken() async {
    final prefs = await SharedPreferences.getInstance();
    if (_supportsSecure) {
      try {
        await _secure.delete(tokenKey).timeout(_opTimeout);
      } catch (_) {
        // 忽略：下面仍会清 SharedPreferences，登出不得被安全存储异常打断。
      }
    }
    await prefs.remove(tokenKey);
  }
}
