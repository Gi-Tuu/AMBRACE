import 'dart:async';

import 'package:dio/dio.dart';

/// 计时器工厂（可注入 ⇒ 单测用「手动假计时器」完全确定性驱动，不依赖真实时间）。
typedef XhrTimerFactory = Timer Function(Duration duration, void Function() callback);

/// XHR 适配器的**四相位计时决策**（纯 Dart，无 DOM 依赖 ⇒ VM 可单测）。
///
/// 相位边界（与 `xhr_stream_adapter_web.dart` 一致）：
/// * **P0 收集请求体** —— 预算 `sendTimeout`（由适配器用 `collect().timeout` 施加；本类不驱计时器，
///   只通过 [sendBudget] 供适配器读取，避免与 P1 重复计一份）。
/// * **P1 `xhr.send` → 上传完成** —— `sendTimeout`，自 [beginSend] 起算。真实慢上行在此超时。
///   bodyless 请求**不**调用 [beginSend]（无上传相位），直接 [uploadCompleted]。
/// * **P2 上传完成 → `HEADERS_RECEIVED`** —— `connectTimeout`，**自 [uploadCompleted] 起算**
///   （故慢上行绝不会被等待响应头的 connect 计时抢走）。
/// * **P3 headers → `DONE`** —— `receiveTimeout` 作字节空闲计时（[resetReceive]）。
///
/// 约定：`null` / `Duration.zero` 一律**禁用**该相位计时（与 dio 语义一致），SSE 因此保持流式。
/// 结算（失败/取消/完成）后由适配器调用 [cancelAll]，本类不再起任何新计时器
/// （配合 [isSettled] 双保险，杜绝「晚报 sendTimeout」）。
class XhrUploadPhases {
  XhrUploadPhases({
    required Duration? sendTimeout,
    required Duration? connectTimeout,
    required Duration? receiveTimeout,
    required void Function(DioExceptionType type) onTimeout,
    required bool Function() isSettled,
    XhrTimerFactory timerFactory = Timer.new,
  })  : _sendTimeout = normalize(sendTimeout),
        _connectTimeout = normalize(connectTimeout),
        _receiveTimeout = normalize(receiveTimeout),
        _onTimeout = onTimeout,
        _isSettled = isSettled,
        _timerFactory = timerFactory;

  final Duration? _sendTimeout;
  final Duration? _connectTimeout;
  final Duration? _receiveTimeout;
  final void Function(DioExceptionType type) _onTimeout;
  final bool Function() _isSettled;
  final XhrTimerFactory _timerFactory;

  Timer? _tSend;
  Timer? _tConnect;
  Timer? _tReceive;
  bool _uploadDone = false;
  bool _connectStarted = false;

  /// `null` / `Duration.zero` ⇒ 禁用（返回 null）。
  static Duration? normalize(Duration? v) =>
      (v == null || v == Duration.zero) ? null : v;

  /// P0 收集请求体的预算（`sendTimeout` 归一值）；`null` ⇒ 无预算（禁用）。
  Duration? get sendBudget => _sendTimeout;

  /// P1 是否已结束（上传完成）。
  bool get uploadDone => _uploadDone;

  /// P1 是否已起（仅用于测试/诊断）。
  bool get sendPhaseActive => _tSend != null;

  /// P2 是否已起（仅用于测试/诊断）。
  bool get connectPhaseStarted => _connectStarted;

  /// 启动 P1 计时（在 `xhr.send` 之后调用）。bodyless 请求不应调用。
  void beginSend() {
    if (_isSettled() || _uploadDone) return;
    final d = _sendTimeout;
    if (d == null) return;
    _tSend?.cancel();
    _tSend = _timerFactory(d, () => _onTimeout(DioExceptionType.sendTimeout));
  }

  /// P1→P2 交接：上传阶段结束（幂等：`onload` 与 `onloadend` 都可能触发）。
  void uploadCompleted() {
    if (_uploadDone || _isSettled()) return;
    _uploadDone = true;
    _tSend?.cancel();
    _tSend = null;
    _beginConnect();
  }

  /// P2→P3 交接：收到响应头。清 connect 计时，进入接收空闲计时。
  void headersReceived() {
    _tConnect?.cancel();
    _tConnect = null;
    resetReceive();
  }

  /// P3：字节空闲计时（收到一段字节即重置）。禁用时不设。
  void resetReceive() {
    if (_isSettled()) return;
    final d = _receiveTimeout;
    if (d == null) return;
    _tReceive?.cancel();
    _tReceive = _timerFactory(d, () => _onTimeout(DioExceptionType.receiveTimeout));
  }

  void _beginConnect() {
    if (_connectStarted || _isSettled()) return;
    _connectStarted = true;
    final d = _connectTimeout;
    if (d == null) return;
    _tConnect = _timerFactory(d, () => _onTimeout(DioExceptionType.connectionTimeout));
  }

  /// 结算：清空全部计时器（失败/取消/完成共用）。
  void cancelAll() {
    _tSend?.cancel();
    _tConnect?.cancel();
    _tReceive?.cancel();
    _tSend = null;
    _tConnect = null;
    _tReceive = null;
  }
}
