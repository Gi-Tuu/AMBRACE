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

/// 后端下发的开关展示元数据（A4，2026-09-20）：目录（标题/说明/分组/顺序）改由后端下发，
/// App 只在后端没有数据时回落本地硬编码目录，保证离线/老后端不丢文案。
class FlagMetaInfo {
  /// 展示标题（后端已按请求语言选好 zh/en）
  final String title;

  /// 一句话说明
  final String desc;

  /// 分组 id（与后端 flag_catalog 一致；未登记键为 'other'）
  final String group;

  /// 分组顺序（小的在前）
  final int groupOrder;

  /// 组内顺序（小的在前）
  final int order;

  /// true = 常用开关（直显）；false = 收进折叠的高级区
  final bool visible;

  const FlagMetaInfo({
    required this.title,
    required this.desc,
    required this.group,
    required this.groupOrder,
    required this.order,
    required this.visible,
  });
}

class FeatureFlagService extends ChangeNotifier {
  FeatureFlagService._();
  static final FeatureFlagService instance = FeatureFlagService._();

  /// 客户端已知并参与「即时生效」的 flag 默认值（2026-08-24 织网 3D P2：weave_3d 默认开）。
  /// 与后端 AGENT_FLAGS['weave_3d'] 保持一致，保证未登录/非主账号兜底也默认开。
  static const Map<String, bool> _knownDefaults = {
    'weave_3d': true,
  };

  final Map<
      String,
      ({
        bool enabled,
        String source,
        String? type,
        num? value,
        FlagMetaInfo? meta,
        String? scope,
        bool? userEnabled
      })> _flags = {};

  /// 读取某 flag 的**生效值**（A5 账号独立 · 2026-09-21 客户端折叠批）：user-scoped 键有账号覆盖时取
  /// `user_enabled`，否则取全局值；非用户级键 / 老后端未下发 scope 或 user_enabled 时与改动前逐字节一致。
  bool isEnabled(String key) {
    final e = _flags[key];
    if (e == null) return _knownDefaults[key] ?? false;
    if (e.scope == 'user' && e.userEnabled != null) return e.userEnabled!;
    return e.enabled;
  }

  /// 全局值（服务器级现值）：服务器级键的唯一取值来源，也是用户级键无覆盖时的回落值。
  bool globalEnabledOf(String key) => _flags[key]?.enabled ?? (_knownDefaults[key] ?? false);

  /// flag 来源（db=被 DB 覆盖 / default=硬编码默认），未加载时用默认值。
  String sourceOf(String key) => _flags[key]?.source ?? 'default';

  /// flag 注册类型（"bool"/"int"/...）；老后端未下发时为 null。
  String? flagType(String key) => _flags[key]?.type;

  /// flag 数值（int/float，仅非 bool 类型有意义）；老后端未下发时为 null。
  num? flagValue(String key) => _flags[key]?.value;

  /// 是否已从服务器加载到该 key（用于 UI 区分「可见白名单」与「高级开关」列表）。
  bool contains(String key) => _flags.containsKey(key);

  /// 当前已加载的全部 key（供高级开关列表排序展示）。
  List<String> get keys => _flags.keys.toList();

  /// 后端下发的展示元数据（老后端/未下发 = null，调用方回落本地目录）。
  FlagMetaInfo? metaOf(String key) => _flags[key]?.meta;

  /// 是否按账号生效（由后端 scope=='user' 推导）；false = 服务器级（改动影响本服务器所有账号）。
  bool isUserScoped(String key) => _flags[key]?.scope == 'user';

  /// 用户级覆盖值（仅 user-scoped 旗标有意义）：true=用户开启、false=用户关闭、null=未覆盖（回落全局）。
  bool? userEnabledOf(String key) => _flags[key]?.userEnabled;

  /// 后端下发的所有常用开关 key（visible=true），按元数据的 order 升序；为空表示无后端数据。
  List<String> get visibleKeys {
    final out = _flags.entries
        .where((e) => e.value.meta?.visible == true)
        .toList();
    out.sort((a, b) => (a.value.meta!.order).compareTo(b.value.meta!.order));
    return out.map((e) => e.key).toList();
  }

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
            type: f['type'] as String?,
            value: f['value'] as num?,
            meta: _parseMeta(f['meta']),
            scope: f['scope'] as String?,
            userEnabled: f['user_enabled'] as bool?,
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
    final prevEntry = _flags[key];
    final prev = prevEntry?.enabled ?? false;
    _flags[key] = (
      enabled: enabled,
      source: 'db',
      type: prevEntry?.type,
      value: prevEntry?.value,
      meta: prevEntry?.meta,
      scope: prevEntry?.scope,
      userEnabled: prevEntry?.userEnabled,
    );
    notifyListeners();
    try {
      await ApiClient().updateFeatureFlag(key, enabled);
      return true;
    } catch (_) {
      _flags[key] = (
        enabled: prev,
        source: 'db',
        type: prevEntry?.type,
        value: prevEntry?.value,
        meta: prevEntry?.meta,
        scope: prevEntry?.scope,
        userEnabled: prevEntry?.userEnabled,
      );
      notifyListeners();
      return false;
    }
  }

  /// 测试/调试用：直接写本地缓存（不访问服务器；生产代码不调用）。
  /// 供 widget 测试固定某 flag 的取值（如织网 3D 强制关闭以测 2.5D 画布）。
  @visibleForTesting
  void debugSetLocal(String key, bool enabled,
      {String? type, num? value, FlagMetaInfo? meta, String? scope, bool? userEnabled}) {
    _flags[key] =
        (enabled: enabled, source: 'test', type: type, value: value, meta: meta, scope: scope, userEnabled: userEnabled);
    notifyListeners();
  }

  /// 解析后端 meta 字段（可空/结构异常 → null，调用方回落本地目录）。
  static FlagMetaInfo? _parseMeta(Object? raw) {
    if (raw is! Map) return null;
    final title = raw['title'] as String? ?? '';
    if (title.isEmpty) return null;
    return FlagMetaInfo(
      title: title,
      desc: raw['desc'] as String? ?? '',
      group: raw['group'] as String? ?? 'other',
      groupOrder: (raw['group_order'] as num?)?.toInt() ?? 999,
      order: (raw['order'] as num?)?.toInt() ?? 9999,
      visible: raw['visible'] as bool? ?? false,
    );
  }
}
