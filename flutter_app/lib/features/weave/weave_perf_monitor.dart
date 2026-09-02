// 织网 3D · 低端机自动降级判定（纯逻辑，可单测）
//
// 2026-08-24（织网 3D P2）：flutter_scene 初始化/渲染异常用 try/catch 回退 2.5D（见视图 State）；
// 本文件只封装「持续低帧率 → 一次性降级」的纯判定，供 3D 视图按帧调用。
//
// 2026-08-24（织网 3D P3，灰块修复）：加入 [warmupFrames] 暖机帧宽限——首帧 shader 编译、
// 分批纹理预热等瞬态低谷不参与帧率判定，避免误触发一次性降级（直接打到 2.5D 的根因之一）。
//
// 不依赖 flutter_scene / GPU；使用调用方传入的「单调毫秒时间戳」（如 Stopwatch/Ticker 的
// elapsed.inMilliseconds），与钟/时区无关，故可纯逻辑单测。
class WeaveDegradeMonitor {
  WeaveDegradeMonitor({
    this.windowMs = 2000,
    this.minFps = 30.0,
    this.warmupFrames = 0,
  })  : assert(windowMs > 0, 'windowMs must be positive'),
        assert(minFps > 0, 'minFps must be positive'),
        assert(warmupFrames >= 0, 'warmupFrames must be >= 0'),
        _warmupRemaining = warmupFrames;

  /// 帧率窗口时长（毫秒）。持续约 [windowMs] 的平均帧率低于阈值才判定降级。
  final int windowMs;

  /// 平均帧率低于此值（fps）判定为低端机。
  final double minFps;

  /// 暖机宽限帧数：前 [warmupFrames] 帧不参与帧率统计（首帧 shader 编译/纹理预热等瞬态
  /// 低谷会被这层过滤掉），避免误降级。默认 0 保持旧判定行为（供既有单测不变）。
  final int warmupFrames;

  int _warmupRemaining;

  int? _windowStart; // null 表示尚未开始采样（用 nullable 避免与 0 时间戳冲突）
  int _frames = 0;
  bool _degraded = false;

  /// 是否已判定降级（一次性：触发后保持 true，避免频繁抖动）。
  bool get shouldDegrade => _degraded;

  /// 记录一帧渲染发生（[nowMs] 为单调毫秒时间戳，≥0）。
  ///
  /// 前 [warmupFrames] 帧只递减宽限计数、不参与统计；窗口未满（< [windowMs]）不判定；
  /// 窗口满后按平均帧率判定。若平均帧率 [< minFps] 则返回 true 并一次性降级；
  /// 否则重置窗口继续监测、返回 false。
  bool recordFrame(int nowMs) {
    if (_degraded) return true;
    if (_warmupRemaining > 0) {
      _warmupRemaining--;
      return false;
    }
    _windowStart ??= nowMs;
    _frames++;
    final spanMs = nowMs - _windowStart!;
    if (spanMs < windowMs) return false; // 窗口未满，继续累积
    final avgFps = _frames / (spanMs / 1000.0);
    if (avgFps < minFps) {
      _degraded = true;
      return true;
    }
    // 帧率足够：重置窗口继续监测（仍不降级）。
    _frames = 0;
    _windowStart = nowMs;
    return false;
  }

  /// 复位（例如重建/切换后重新监测）。
  void reset() {
    _windowStart = null;
    _frames = 0;
    _degraded = false;
    _warmupRemaining = warmupFrames;
  }

  /// 纯判定：给定平均帧率是否过低（≥ [minFps] 的边界视为不降级）。
  static bool isLowFps(double averageFps, {double minFps = 30.0}) =>
      averageFps < minFps;
}

// ───────────────────── 3D 分档降级（full → light → 2D） ─────────────────────

/// 3D 渲染档位（一次性下坡，不抖动）：full（纹理+连线+节点球）→ light（纯色圆点+连线+低细分球）。
enum WeaveRenderTier {
  /// full 档：当前实现（卡片纹理 + 连线 + 节点球，球体细分 12/8）。
  full,

  /// light 档：3D 纯色圆点（weaveNodeColor 纯色球）+ 连线 + 聚类泡，节点球低细分（8/6）。
  light,
}

/// 记录一帧后发生的档位变化（供视图按事件做副作用与提示）。
enum WeaveDegradeEvent {
  /// 档位未变化（仍在当前档监测中）。
  none,

  /// full → light（已切换轻量模式）：停纹理预热、清空纹理缓存、提示「已切换轻量模式」。
  switchedToLight,

  /// light → 2.5D（持续低帧率，回退 2D）：提示「持续低帧率」并切 2.5D 视图。
  fallbackTo2D,
}

/// 两段式低端机降级判定（纯逻辑，可单测）：
///
/// - full 档：约 [windowMs] 平均帧率 < [fullMinFps]（且暖机宽限结束）→ 切 light（一次性，不回退）；
/// - light 档：约 [windowMs] 平均帧率 < [lightMinFps] → 已降级 2.5D（一次性，不回退）。
///
/// 内部用两个 [WeaveDegradeMonitor] 实例分别监测两档；当前处于哪一档由 [tier] 表示。
/// 一次性下坡（不自动升回 full）由 [tier] 只前进不退后保证，避免抖动。
class WeaveRenderDegrader {
  WeaveRenderDegrader({
    this.windowMs = 2000,
    this.fullMinFps = 30.0,
    this.lightMinFps = 20.0,
    this.warmupFrames = 0,
  })  : assert(windowMs > 0, 'windowMs must be positive'),
        assert(fullMinFps > 0, 'fullMinFps must be positive'),
        assert(lightMinFps > 0, 'lightMinFps must be positive'),
        assert(warmupFrames >= 0, 'warmupFrames must be >= 0'),
        _full = WeaveDegradeMonitor(
          windowMs: windowMs,
          minFps: fullMinFps,
          warmupFrames: warmupFrames,
        ),
        _light = WeaveDegradeMonitor(windowMs: windowMs, minFps: lightMinFps);

  /// 帧率窗口时长（毫秒），full/light 两档共用。
  final int windowMs;

  /// full 档降 light 的平均帧率阈值（默认 30fps）。
  final double fullMinFps;

  /// light 档回退 2.5D 的平均帧率阈值（默认 20fps）。
  final double lightMinFps;

  /// full 档暖机宽限帧数（首帧 shader 编译/纹理预热等瞬态低谷不参与判定；light 档不做宽限）。
  final int warmupFrames;

  final WeaveDegradeMonitor _full;
  final WeaveDegradeMonitor _light;

  WeaveRenderTier _tier = WeaveRenderTier.full;
  bool _degradedTo2D = false;

  /// 当前渲染档位（full 或 light）。
  WeaveRenderTier get tier => _tier;

  /// 是否已回退 2.5D（一次性：触发后保持 true，避免频繁抖动）。
  bool get degradedTo2D => _degradedTo2D;

  /// 记录一帧渲染发生（[nowMs] 为单调毫秒时间戳，≥0），返回档位变化事件。
  ///
  /// - 当前为 full：交给 full 档监视器；持续低帧率 (< [fullMinFps]) 则切 light，返回
  ///   [WeaveDegradeEvent.switchedToLight]（此后不再回 full）。
  /// - 当前为 light：交给 light 档监视器；持续低帧率 (< [lightMinFps]) 则标记已回退 2.5D，
  ///   返回 [WeaveDegradeEvent.fallbackTo2D]。
  /// - 已回退 2.5D：恒返回 [WeaveDegradeEvent.none]。
  WeaveDegradeEvent recordFrame(int nowMs) {
    if (_degradedTo2D) return WeaveDegradeEvent.none;
    if (_tier == WeaveRenderTier.full) {
      if (_full.recordFrame(nowMs)) {
        _tier = WeaveRenderTier.light;
        return WeaveDegradeEvent.switchedToLight;
      }
      return WeaveDegradeEvent.none;
    }
    // light 档：不再监测回 full（一次性下坡），只判断是否回退 2.5D。
    if (_light.recordFrame(nowMs)) {
      _degradedTo2D = true;
      return WeaveDegradeEvent.fallbackTo2D;
    }
    return WeaveDegradeEvent.none;
  }

  /// 复位（重建/切换后重新监测，回到 full 档并重新暖机）。
  void reset() {
    _full.reset();
    _light.reset();
    _tier = WeaveRenderTier.full;
    _degradedTo2D = false;
  }

  /// 直接设定当前档位（手动固定档/初始档用，2026-08-24 手动档位切换）。
  ///
  /// - [light3d]：初始即 light 档（纯色圆点+低细分球，不预热纹理）；
  /// - [full3d] 与 auto：初始 full 档。
  /// 手动模式下本实例不参与自动降级（视图不调用 [recordFrame]）；切回 auto 时应调用 [reset]
  /// 重新启用并从头暖机。设定后只改 [tier]，不影响自动监测的语义（由调用方保证不再 [recordFrame]）。
  void startAt(WeaveRenderTier tier) {
    _tier = tier;
  }
}
