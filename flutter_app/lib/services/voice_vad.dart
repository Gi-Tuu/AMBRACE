/// 轻量客户端 VAD（Phase 1，2026-08-31）
///
/// 说明：本实现为**纯 Dart 能量（dBFS 阈值）端点检测**，不引入 ONNX/Silero 原生库，
/// 不破坏现有 Android 构建（无需 gradle/so/APK 体积变化），可被组件/单测覆盖。
///
/// 决策（见交付报告的 VAD 选型结论）：Silero ONNX（flutter_onnxruntime / sherpa_onnx）
/// 精度更高但带原生 so 与运行时依赖，需在真机验证 Android 集成与 CPU 功耗；本部署按
/// 「能落地且不破坏现有 Android 构建」优先，先以能量端点检测交付，并把「按住说话」
/// 作为显式降级开关。后续可在 [VadGate] 基础上换用 Silero 打分（接口不变）。
///
/// 工作方式：把 record 插件的 `onAmplitudeChanged` 回调（dBFS，0=最大/负值越小越轻）
/// 逐次喂给 [feed]，返回 [VadEvent]；语音开始（持续超过 [speechStartHoldMs]）出
/// speechStart，静音超过 [silenceMs] 出 speechEnd（可据此截断一段并提交）。
library;

/// VAD 事件类型。
enum VadEventType { none, speechStart, speechEnd }

/// VAD 判定结果（[VadEventType.speechStart]/[VadEventType.speechEnd] 时携带时间点）。
class VadEvent {
  const VadEvent(this.type, {this.startMs = 0, this.endMs = 0});

  final VadEventType type;
  final int startMs;
  final int endMs;

  bool get isSpeechStart => type == VadEventType.speechStart;
  bool get isSpeechEnd => type == VadEventType.speechEnd;
}

/// 能量端点检测状态机（纯 Dart，可单测）。
class VadGate {
  VadGate({
    this.speechThresholdDb = -45.0,
    this.speechStartHoldMs = 150,
    this.silenceMs = 700,
    this.maxSegmentMs = 30000,
  });

  /// 说话的 dBFS 阈值：current >= 该值视为「有声音」。
  /// record 的 current 为 dBFS（0=max，安静时通常在 -120..-60，说话常在 -40..-10）。
  final double speechThresholdDb;

  /// 持续超过该时长才判定为「语音开始」——避免单次噪声尖峰误触发。
  final int speechStartHoldMs;

  /// 静音持续该时长判定为「语音结束」（提交一段）。
  final int silenceMs;

  /// 单段上限，超时强制截断（防大段长音）。
  final int maxSegmentMs;

  // 内部状态
  bool _speech = false;
  int? _candidateStartMs;
  int _speechStartMs = 0;
  int _lastActiveMs = 0;

  /// 当前是否判定为「正在说话」。
  bool get isSpeech => _speech;

  /// 输入一次幅度采样（dBFS，[nowMs] 为单调时钟毫秒）。返回事件（none 表示无变化）。
  VadEvent feed(double db, int nowMs) {
    final active = db >= speechThresholdDb;
    if (!_speech) {
      // waiting：寻找达到阈值且持续一段的候选起点
      if (active) {
        _candidateStartMs ??= nowMs;
        if (nowMs - _candidateStartMs! >= speechStartHoldMs) {
          _speech = true;
          _speechStartMs = _candidateStartMs!;
          _lastActiveMs = nowMs;
          return VadEvent(VadEventType.speechStart, startMs: _speechStartMs);
        }
      } else {
        _candidateStartMs = null;
      }
      return const VadEvent(VadEventType.none);
    }

    // speech：跟踪活跃；静音超时或超长 → 结束
    if (active) {
      _lastActiveMs = nowMs;
    }
    if (nowMs - _lastActiveMs >= silenceMs || nowMs - _speechStartMs >= maxSegmentMs) {
      final startMs = _speechStartMs;   // 必须在 _reset() 前捕获
      final endMs = _lastActiveMs;
      _reset();
      return VadEvent(VadEventType.speechEnd, startMs: startMs, endMs: endMs);
    }
    return const VadEvent(VadEventType.none);
  }

  /// 强制结束当前段（用户手动停止 / 打断时调用）。
  VadEvent? forceEnd(int nowMs) {
    if (!_speech) return null;
    final endMs = _lastActiveMs == 0 ? nowMs : _lastActiveMs;
    final startMs = _speechStartMs;
    _reset();
    return VadEvent(VadEventType.speechEnd, startMs: startMs, endMs: endMs);
  }

  /// 重置状态（开始新的监听轮次）。
  void reset() => _reset();

  void _reset() {
    _speech = false;
    _candidateStartMs = null;
    _speechStartMs = 0;
    _lastActiveMs = 0;
  }
}
