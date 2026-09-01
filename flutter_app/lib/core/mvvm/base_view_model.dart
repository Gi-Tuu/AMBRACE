import 'package:flutter/foundation.dart';

/// MVVM 基类（F7，2026-08-31）：极薄的可通知 ViewModel，封装 busy/error 状态守卫。
///
/// 约定：
/// - 全局应用状态仍走 lib/providers/（ChangeNotifier + provider 包），本基类服务于
///   「页面/区段级」局部 UI 状态（加载中/错误/重试），两者互补不替代；
/// - 异步逻辑一律经 [guard] 执行：busy 置位 → 执行 → 失败写入 [error] 并通知，
///   避免每个页面手写 try/catch + mounted 判断样板；
/// - dispose 后的 notify 一律吞掉（ViewModel 生命周期晚于异步回调是常见崩溃源）。
class BaseViewModel extends ChangeNotifier {
  bool _busy = false;
  String? _error;
  bool _disposed = false;

  /// 是否有异步任务在执行（guard 进行中）
  bool get busy => _busy;

  /// 最近一次失败的错误描述（guard 捕获；成功/未开始为 null）
  String? get error => _error;

  bool get hasError => _error != null;

  /// 统一异步守卫。
  ///
  /// - [run]：业务异步体；
  /// - [errorPrefix]：错误信息前缀（如「加载失败」），缺省用原始异常文本；
  /// - [silent]：true 时不记录 error（静默降级场景，仅解除 busy）；
  /// - 返回 run 的结果；失败返回 null（异常已捕获）。
  Future<T?> guard<T>(Future<T> Function() run, {String? errorPrefix, bool silent = false}) async {
    if (_busy) return null;
    _busy = true;
    _error = null;
    _safeNotify();
    try {
      return await run();
    } catch (e) {
      if (!silent) _error = errorPrefix == null ? '$e' : '$errorPrefix: $e';
      return null;
    } finally {
      _busy = false;
      _safeNotify();
    }
  }

  /// 手动清除错误态（重试入口常用）
  void clearError() {
    if (_error == null) return;
    _error = null;
    _safeNotify();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  void _safeNotify() {
    if (_disposed) return;
    notifyListeners();
  }
}
