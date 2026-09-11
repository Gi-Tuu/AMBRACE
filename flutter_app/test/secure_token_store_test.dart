import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/services/secure_token_store.dart';

/// P2-A（2026-09-11 全量审查）：JWT 迁移安全存储的迁移 / 降级逻辑单测。
///
/// 通过注入 [SecureKeyValueStore] 替身，覆盖：
/// 旧明文一次性迁移、幂等、安全存储优先、写入/读取/删除的异常降级、
/// Web/桌面平台降级，以及真实 flutter_secure_storage 11.x API 的往返校验。

/// 内存替身（模拟可用的 Keystore/Keychain）。
class _FakeSecureStore implements SecureKeyValueStore {
  _FakeSecureStore([Map<String, String>? initial]) : data = {...?initial};

  final Map<String, String> data;
  int readCount = 0;
  int writeCount = 0;
  int deleteCount = 0;

  @override
  Future<String?> read(String key) async {
    readCount++;
    return data[key];
  }

  @override
  Future<void> write(String key, String value) async {
    writeCount++;
    data[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    deleteCount++;
    data.remove(key);
  }
}

/// 静默失败替身：write 不生效、read 永远空（模拟某些平台上安全存储静默失败）。
class _SilentFailSecureStore implements SecureKeyValueStore {
  @override
  Future<String?> read(String key) async => null;

  @override
  Future<void> write(String key, String value) async {}

  @override
  Future<void> delete(String key) async {}
}

/// 抛异常替身（模拟 Keystore/Keychain 不可用）。
class _ThrowingSecureStore implements SecureKeyValueStore {
  @override
  Future<String?> read(String key) async => throw Exception('keystore unavailable');

  @override
  Future<void> write(String key, String value) async =>
      throw Exception('keystore unavailable');

  @override
  Future<void> delete(String key) async =>
      throw Exception('keystore unavailable');
}

/// 永不完成替身：模拟平台通道无响应（Future 永不完成）。
/// 回归用例：调用方必须靠超时降级，绝不能被挂死（曾导致登录页 pumpAndSettle 超时）。
class _HangingSecureStore implements SecureKeyValueStore {
  @override
  Future<String?> read(String key) => Completer<String?>().future;

  @override
  Future<void> write(String key, String value) => Completer<void>().future;

  @override
  Future<void> delete(String key) => Completer<void>().future;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('旧明文 → 安全存储 一次性迁移', () {
    test('升级首启：读到旧明文、写入安全存储、删除 prefs 明文', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'legacy-jwt'});
      final secure = _FakeSecureStore();
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      expect(await store.readToken(), 'legacy-jwt');
      expect(secure.data[SecureTokenStore.tokenKey], 'legacy-jwt');

      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull,
          reason: '迁移成功后必须删除明文');
    });

    test('幂等：连续两次读只迁移一次，明文不再回写', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'jwt-1'});
      final secure = _FakeSecureStore();
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      expect(await store.readToken(), 'jwt-1');
      expect(await store.readToken(), 'jwt-1');
      expect(secure.writeCount, 1, reason: '第二次读应直接命中安全存储');
      expect(secure.data[SecureTokenStore.tokenKey], 'jwt-1');
    });

    test('安全存储优先：安全存储与 prefs 都有值时取安全存储并清掉残留明文', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'stale-plain'});
      final secure = _FakeSecureStore({'auth_token': 'secure-jwt'});
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      expect(await store.readToken(), 'secure-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);
    });

    test('两边都为空 → 返回空串（未登录），不写任何东西', () async {
      SharedPreferences.setMockInitialValues({});
      final secure = _FakeSecureStore();
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      expect(await store.readToken(), '');
      expect(secure.writeCount, 0);
    });

    test('安全存储静默失败：不删明文、仍返回旧 token，下次启动可重试迁移', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'legacy-jwt'});
      final store =
          SecureTokenStore(secure: _SilentFailSecureStore(), supportsSecure: true);

      expect(await store.readToken(), 'legacy-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), 'legacy-jwt',
          reason: '读回校验未通过时必须保留明文兜底');
    });
  });

  group('写入 / 删除', () {
    test('writeToken：写安全存储并清除 prefs 明文', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'old-plain'});
      final secure = _FakeSecureStore();
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      await store.writeToken('new-jwt');
      expect(secure.data[SecureTokenStore.tokenKey], 'new-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);
    });

    test('writeToken 降级：安全存储抛异常时回退 prefs，登录流程不受影响', () async {
      SharedPreferences.setMockInitialValues({});
      final store =
          SecureTokenStore(secure: _ThrowingSecureStore(), supportsSecure: true);

      await store.writeToken('fallback-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), 'fallback-jwt');
    });

    test('deleteToken：安全存储与 prefs 两处都清', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'plain'});
      final secure = _FakeSecureStore({'auth_token': 'secure-jwt'});
      final store = SecureTokenStore(secure: secure, supportsSecure: true);

      await store.deleteToken();
      expect(secure.data[SecureTokenStore.tokenKey], isNull);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);
    });

    test('deleteToken 降级：安全存储抛异常仍清 prefs 且不抛出', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'plain'});
      final store =
          SecureTokenStore(secure: _ThrowingSecureStore(), supportsSecure: true);

      await expectLater(store.deleteToken(), completes);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);
    });
  });

  group('读取异常降级（不把用户登出、不崩）', () {
    test('安全存储抛异常 + prefs 有旧明文 → 返回旧明文', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'legacy-jwt'});
      final store =
          SecureTokenStore(secure: _ThrowingSecureStore(), supportsSecure: true);

      expect(await store.readToken(), 'legacy-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), 'legacy-jwt',
          reason: '降级路径不得删除明文兜底');
    });

    test('安全存储抛异常 + prefs 为空 → 返回空串且不抛出', () async {
      SharedPreferences.setMockInitialValues({});
      final store =
          SecureTokenStore(secure: _ThrowingSecureStore(), supportsSecure: true);

      await expectLater(store.readToken(), completion(''));
    });
  });

  group('不支持安全存储的平台（Web / 桌面）降级', () {
    test('读写删全部只走 SharedPreferences，安全存储完全不被触碰', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'plain-jwt'});
      final secure = _FakeSecureStore();
      final store = SecureTokenStore(secure: secure, supportsSecure: false);

      expect(await store.readToken(), 'plain-jwt');
      await store.writeToken('plain-2');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), 'plain-2');
      await store.deleteToken();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);

      expect(secure.readCount, 0);
      expect(secure.writeCount, 0);
      expect(secure.deleteCount, 0);
    });

    test('platformSupportsSecure 只认 Android / iOS', () {
      final saved = debugDefaultTargetPlatformOverride;
      addTearDown(() => debugDefaultTargetPlatformOverride = saved);
      for (final entry in <TargetPlatform, bool>{
        TargetPlatform.android: true,
        TargetPlatform.iOS: true,
        TargetPlatform.windows: false,
        TargetPlatform.linux: false,
        TargetPlatform.macOS: false,
      }.entries) {
        debugDefaultTargetPlatformOverride = entry.key;
        expect(SecureTokenStore.platformSupportsSecure, entry.value,
            reason: '${entry.key} 应${entry.value ? '' : '不'}支持安全存储');
      }
    });
  });

  group('安全存储无响应（平台通道挂起）→ 超时降级不挂死', () {
    test('读/写/删全部超时降级，调用方不会被挂死', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'legacy-jwt'});
      final store = SecureTokenStore(
        secure: _HangingSecureStore(),
        supportsSecure: true,
        opTimeout: const Duration(milliseconds: 20),
      );

      expect(await store.readToken(), 'legacy-jwt', reason: '读超时 → 回退明文');

      await store.writeToken('new-jwt');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), 'new-jwt',
          reason: '写超时 → 回退 prefs，登录流程可用');

      await expectLater(store.deleteToken(), completes);
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull,
          reason: '删超时 → 仍清 prefs');
    });
  });

  group('真实 flutter_secure_storage 11.x API 往返（mock 平台）', () {
    late TargetPlatform? saved;

    setUp(() {
      saved = debugDefaultTargetPlatformOverride;
      debugDefaultTargetPlatformOverride = TargetPlatform.android;
      FlutterSecureStorage.setMockInitialValues({});
    });

    tearDown(() {
      debugDefaultTargetPlatformOverride = saved;
    });

    test('FlutterSecureKeyValueStore 读写删往返', () async {
      const real = FlutterSecureKeyValueStore();
      expect(await real.read(SecureTokenStore.tokenKey), isNull);
      await real.write(SecureTokenStore.tokenKey, 'jwt-x');
      expect(await real.read(SecureTokenStore.tokenKey), 'jwt-x');
      await real.delete(SecureTokenStore.tokenKey);
      expect(await real.read(SecureTokenStore.tokenKey), isNull);
    });

    test('端到端：旧明文经真实插件（mock 平台）迁移且再次读取命中安全存储', () async {
      SharedPreferences.setMockInitialValues({'auth_token': 'legacy-e2e'});
      final store = SecureTokenStore(); // 走默认 platformSupportsSecure（android → true）
      expect(store.supportsSecure, isTrue);

      expect(await store.readToken(), 'legacy-e2e');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(SecureTokenStore.tokenKey), isNull);
      expect(await store.readToken(), 'legacy-e2e');

      await store.deleteToken();
      expect(await store.readToken(), '');
    });
  });
}
