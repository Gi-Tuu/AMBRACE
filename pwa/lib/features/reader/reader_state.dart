// 只读工作台（M1a-2）面板共享的加载状态机：loading / error / empty / data 四态 + retry。
// 纪律：
// - 代次（gen）防竞态：慢 A 快 B 时旧请求迟到结果一律丢弃，不覆盖新状态；
// - CancelToken 真实取消：切换角色/模块/账号/面板销毁时在飞请求被真实取消；
// - 被取消**不算错误**（静默丢弃），不闪错误条；
// - 纯面板侧状态，不碰全局登录/会话状态（401 清理由 WorkspacePage onUnauthorized 统一处理）。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

enum ReaderStatus { loading, error, empty, data }

/// 取消判定：dio 直抛 cancel，或 AmbApiClient 归一后的 ApiException(cancelled)。
bool isReaderCancel(Object e) {
  if (e is DioException) {
    if (e.type == DioExceptionType.cancel) return true;
    final err = e.error;
    if (err is ApiException && err.kind == 'cancelled') return true;
  }
  return e is ApiException && e.kind == 'cancelled';
}

class ReaderLoader<T> extends ChangeNotifier {
  ReaderStatus status = ReaderStatus.loading;
  T? data;
  Object? error;

  int _gen = 0;
  CancelToken? _ct;

  /// 当前代次：首屏/刷新/切筛选会 +1。上层「续页」这类自己发起请求的动作，
  /// 用它做归属判定（在飞的旧续页在刷新后必须整页丢弃，连元数据都不能提交）。
  int get generation => _gen;

  /// 重新拉取。[fetch] 收到一个 CancelToken 并必须透传给 ReaderApi；
  /// [isEmpty] 判定空态（缺省：List 且为空）。
  ///
  /// 返回**本次结果是否被采纳**：true = 已成为当前状态；代次被判过期、被取消、
  /// 或出错时为 false。调用方（面板）必须据此提交 total/游标等元数据——
  /// 元数据与数据必须同代次提交，否则旧刷新会污染新列表。
  Future<bool> load(
    Future<T> Function(CancelToken ct) fetch, {
    bool Function(T data)? isEmpty,
  }) async {
    final gen = ++_gen;
    final prev = _ct;
    if (prev != null && !prev.isCancelled) prev.cancel('superseded');
    final ct = CancelToken();
    _ct = ct;
    status = ReaderStatus.loading;
    error = null;
    notifyListeners();
    var committed = false;
    try {
      final d = await fetch(ct);
      if (gen != _gen) return false; // 迟到结果丢弃
      data = d;
      status = _statusFor(d, isEmpty);
      committed = true;
    } on Object catch (e) {
      if (gen != _gen) return false;
      if (isReaderCancel(e)) return false; // 取消静默
      error = e;
      status = ReaderStatus.error;
    } finally {
      if (identical(_ct, ct)) _ct = null;
    }
    notifyListeners();
    return committed;
  }

  /// 续页提交：把**追加后的完整列表**写回来并通知视图。
  /// 调用方必须先自证代次仍当前（`generation` 未变）再调用，否则旧页会污染新列表。
  void commitData(T d, {bool Function(T data)? isEmpty}) {
    data = d;
    error = null;
    status = _statusFor(d, isEmpty);
    notifyListeners();
  }

  ReaderStatus _statusFor(T? d, bool Function(T data)? isEmpty) {
    if (d == null) return ReaderStatus.empty;
    final empty = isEmpty?.call(d) ?? (d is List && d.isEmpty);
    return empty ? ReaderStatus.empty : ReaderStatus.data;
  }

  /// 作废代次并取消在飞请求（切角色/换号/面板销毁）。
  void cancel() {
    _gen++;
    final c = _ct;
    _ct = null;
    if (c != null && !c.isCancelled) c.cancel('disposed');
  }

  @override
  void dispose() {
    cancel();
    super.dispose();
  }
}
