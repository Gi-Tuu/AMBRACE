// 运行时 Feature Flag 客户端缓存（2026-08-24，织网 3D P0；P2 转默认开）
//
// 服务器 AGENT_FLAGS 是真源；本服务是客户端本地缓存，供界面「即时生效」：
// - 画布页读 `isEnabled('weave_3d')` 选择 2D/3D 视图（默认 true = 3D；低端机自动降级 2.5D）；
// - 服务器功能开关页读/写本缓存，切换后通知监听方（画布）重建视图。
//
// 说明：getFeatureFlags 仅主账号可调（非主账号 403 静默保留缓存），因此纯本地默认值
// 可在无网络/无权限时兜底（P2 起 weave_3d 默认开，与后端 AGENT_FLAGS 一致）。
import 'package:flutter/foundation.dart' show ChangeNotifier, visibleForTesting;

import 'package:ai_companion/services/api_client.dart';

class FeatureFlagService extends ChangeNotifier {
  FeatureFlagService._();
  static final FeatureFlagService instance = FeatureFlagService._();

  /// 客户端已知并参与「即时生效」的 flag 默认值（2026-08-24 织网 3D P2：weave_3d 默认开）。
  /// 与后端 AGENT_FLAGS['weave_3d'] 保持一致，保证未登录/非主账号兜底也默认开。
  static const Map<String, bool> _knownDefaults = {
    'weave_3d': true,
  };

  final Map<String, ({bool enabled, String source})> _flags = {};

  /// 读取某 flag：默认 false（未加载/未知 key 均视为关）。
  bool isEnabled(String key) => _flags[key]?.enabled ?? (_knownDefaults[key] ?? false);

  /// flag 来源（db=被 DB 覆盖 / default=硬编码默认），未加载时用默认值。
  String sourceOf(String key) => _flags[key]?.source ?? 'default';

  /// 是否已从服务器加载到该 key（用于 UI 区分「可见白名单」与「高级开关」列表）。
  bool contains(String key) => _flags.containsKey(key);

  /// 当前已加载的全部 key（供高级开关列表排序展示）。
  List<String> get keys => _flags.keys.toList();

  /// 从服务器拉取全部 runtime flag 并刷新缓存；失败静默保留现有缓存。
  Future<void> refresh() async {
    try {
      final flags = await ApiClient().getFeatureFlags();
      _flags.clear();
      for (final f in flags) {
        final k = f['key'] as String? ?? '';
        if (k.isNotEmpty) {
          _flags[k] = (
            enabled: (f['enabled'] as bool?) ?? false,
            source: f['source'] as String? ?? 'default',
          );
        }
      }
      notifyListeners();
    } catch (_) {
      // 网络失败 / 非主账号 403：静默保留缓存（默认值兜底）
    }
  }

  /// 切换 flag：先乐观更新本地缓存（画布/页面即时生效），再写服务器；
  /// 失败回滚并返回 false。
  Future<bool> setFlag(String key, bool enabled) async {
    final prev = _flags[key]?.enabled ?? false;
    _flags[key] = (enabled: enabled, source: 'db');
    notifyListeners();
    try {
      await ApiClient().updateFeatureFlag(key, enabled);
      return true;
    } catch (_) {
      _flags[key] = (enabled: prev, source: 'db');
      notifyListeners();
      return false;
    }
  }

  /// 测试/调试用：直接写本地缓存（不访问服务器；生产代码不调用）。
  /// 供 widget 测试固定某 flag 的取值（如织网 3D 强制关闭以测 2.5D 画布）。
  @visibleForTesting
  void debugSetLocal(String key, bool enabled) {
    _flags[key] = (enabled: enabled, source: 'test');
    notifyListeners();
  }
}
