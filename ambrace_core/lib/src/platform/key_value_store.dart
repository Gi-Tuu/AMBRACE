/// 平台键值存取接口（壳注入）。
///
/// core 内**不做**任何原生持久化：手机壳注入安全存储/SharedPreferences 实现，
/// Web 壳本阶段注入内存实现（刷新回登录页，属明确记录的阶段限制）。
/// 这样 core 的依赖图里不会出现 path_provider / flutter_secure_storage 等原生包。
abstract class KeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

/// 内存实现：仅本进程生命周期（Web 首单用；刷新即丢，**不落** localStorage/缓存/URL）。
class InMemoryKeyValueStore implements KeyValueStore {
  final Map<String, String> _m = <String, String>{};

  @override
  Future<String?> read(String key) async => _m[key];

  @override
  Future<void> write(String key, String value) async {
    _m[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    _m.remove(key);
  }
}
