// 织网 3D · 手动渲染档位模式（纯逻辑，可单测）
//
// 2026-08-24（织网 3D 手动档位切换）：在既有「weave_3d flag 选 2D/3D + 自动降级 full→light→2D」
// 之上，新增手动档位选择器（共 4 档）。用户手选优先；自动降级检测只在「全自动」（auto）模式生效。
//
// 优先级（手动优先于 weave_3d flag 与自动降级状态）：
// - auto（默认）：保持既有 WeaveRenderDegrader 自动降级（full→light→2D 帧率检测），由 weave_3d flag 决定 2D/3D；
// - full3d：强制 3D 全量（纹理+连线+节点球），禁用自动降级监测（不降 light/2D）；
// - light3d：强制 3D 轻量（纯色圆点+低细分球+连线），禁用自动降级监测（初始即 light 档，不预热纹理）；
// - twoD：强制 2.5D 视图。
//
// 手动模式与自动监测互斥：非 auto 时 WeaveRenderDegrader 不参与（停用/重置）；切回 auto 重新启用并从头暖机。
import 'weave_perf_monitor.dart' show WeaveRenderTier;

/// 织网渲染档位模式（手动档位选择）。
enum WeaveViewMode {
  /// 全自动（默认）：保持自动降级 full→light→2D 帧率检测，由 weave_3d flag 决定 2D/3D。
  auto,

  /// 强制 3D 全量（纹理+连线+节点球），禁用自动降级监测（不降 light/2D）。
  full3d,

  /// 强制 3D 轻量（纯色圆点+低细分球+连线），禁用自动降级监测（初始即 light 档，不预热纹理）。
  light3d,

  /// 强制 2.5D 视图。
  twoD;

  /// 持久化 key（shared_preferences 中存储用户所选模式）。
  static const String storageKey = 'weave_view_mode';

  /// 存储缺失/非法时的默认模式（首次默认 auto）。
  static const WeaveViewMode defaultMode = WeaveViewMode.auto;

  /// 手动指定（非 auto）：手动模式下自动降级监测不参与。
  bool get isManual => this != WeaveViewMode.auto;

  /// 是否启用自动降级监测（只有 auto 启用）。
  bool get enableAutoDegrade => this == WeaveViewMode.auto;

  /// 是否强制 3D（full3d/light3d 手动强制 3D，绕过 weave_3d flag）。
  bool get forces3D =>
      this == WeaveViewMode.full3d || this == WeaveViewMode.light3d;

  /// 是否强制 2.5D（twoD）。
  bool get forces2D => this == WeaveViewMode.twoD;

  /// 初始 3D 渲染档位（light3d 初始 light——不预热纹理、纯色圆点；auto/full3d 初始 full）。
  WeaveRenderTier get initialTier =>
      this == WeaveViewMode.light3d ? WeaveRenderTier.light : WeaveRenderTier.full;

  /// 写入 shared_preferences 的稳定字符串（[name]）。
  String get storageValue => name;

  /// 从持久化字符串解析；缺失/未知回退 [defaultMode]。
  static WeaveViewMode fromStorageValue(String? value) {
    for (final m in WeaveViewMode.values) {
      if (m.name == value) return m;
    }
    return WeaveViewMode.auto;
  }
}

/// 决定当前应使用 3D 还是 2.5D 画布（纯逻辑，可单测）。
///
/// 优先级（手动优先于 weave_3d flag 与自动降级状态）：
/// - auto：由 weave_3d flag 决定，且排除自动降级到 2D 的 [force2D]（避免抖动）；
/// - full3d/light3d：手动强制 3D，绕过 flag 与 [force2D]（用户手动选择优先）；
/// - twoD：强制 2.5D。
bool shouldUse3DView(
  WeaveViewMode mode, {
  required bool weave3dFlag,
  required bool force2D,
}) {
  switch (mode) {
    case WeaveViewMode.auto:
      return weave3dFlag && !force2D;
    case WeaveViewMode.full3d:
    case WeaveViewMode.light3d:
      return true;
    case WeaveViewMode.twoD:
      return false;
  }
}
