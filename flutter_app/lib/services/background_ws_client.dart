import "dart:async";
import "dart:convert";

import "package:web_socket_channel/web_socket_channel.dart";

import "bg_poll_guard.dart";

/// 后台保活 WebSocket 客户端（#55，2026-08-23）。
///
/// 在 flutter_background_service 的后台 isolate 里维持与
/// `/api/v1/system/notifications/ws` 的长连接，收到「新 AI 消息 / 主动消息」事件
/// 时回调 [onEvent]，由调用方决定是否弹系统通知。
///
/// 断线重连采用指数退避（1s -> 2s -> 4s -> ...，上限 60s），
/// 连接成功（收到任意数据）即重置退避计数；可配置最大重连次数（0=无限）。
///
/// S6（2026-09-22 P8）在此基础上补两件事，均由 `bg_poll_guard.dart` 的纯逻辑裁决：
/// - **退避抖动**：真实定时器路径按 ±20% 抖动，避免多端/多账号同时重连打回服务端
///   （注入 [EventWsClient.timerFactory] 的调用方要精确时序，走原样退避）；
/// - **心跳探活**：连接建立后每 [EventWsClient.heartbeatInterval] 发一次
///   `{"type":"ping"}`（服务端回 `{"type":"pong"}`），连续 [kHeartbeatMissLimit]
///   个周期收不到任何数据即判半开连接并重连——原来只能等 TCP 超时才发现。
///
/// 纯 Dart，不依赖 Flutter，便于单元测试退避与事件解析。
typedef WsConnector = WebSocketChannel Function(Uri uri);
typedef TimerFactory = Timer Function(Duration duration, void Function() callback);

/// 指数退避计算（纯逻辑，可测）：第 n 次失败等待 baseSeconds * 2^n 秒，上限 maxSeconds。
class ReconnectBackoff {
  const ReconnectBackoff({this.baseSeconds = 1, this.maxSeconds = 60});

  final int baseSeconds;
  final int maxSeconds;

  Duration delayFor(int attempt) {
    final base = baseSeconds < 1 ? 1 : baseSeconds;
    // 位移上限封顶：Dart int 为 64 位，1 << 大数会溢出为 0/负值，导致退避算出 0s。
    // 实际重连次数很小，这里把 shift 钳制到 30，保证结果一定是巨额 → 封顶到 maxSeconds。
    final shift = attempt < 0 ? 0 : (attempt > 30 ? 30 : attempt);
    final raw = base * (1 << shift);
    if (raw <= 0) return Duration.zero;
    final capped = raw > maxSeconds ? maxSeconds : raw;
    return Duration(seconds: capped < 1 ? 1 : capped);
  }
}

/// 从服务端推送事件解析出的通知事件（纯数据，可测）。
class NotifyEvent {
  final int characterId;
  final int sessionId;
  final String content;
  final bool isProactive;

  const NotifyEvent({
    required this.characterId,
    required this.sessionId,
    required this.content,
    required this.isProactive,
  });

  /// 解析服务端事件 payload（如 {"type":"ai_response","data":{"session_id":…,
  /// "character_id":…,"content":…},"is_proactive":true}）；格式不符返回 null。
  static NotifyEvent? tryParse(Object? raw) {
    if (raw is! Map) return null;
    final data = raw["data"];
    if (data is! Map) return null;
    final cid = data["character_id"] as int?;
    if (cid == null) return null;
    final sid = data["session_id"] as int? ?? 0;
    final content = (data["content"] as String? ?? "").toString();
    return NotifyEvent(
      characterId: cid,
      sessionId: sid,
      content: content,
      isProactive: raw["is_proactive"] == true,
    );
  }
}

class EventWsClient {
  EventWsClient({
    required Uri Function() uriBuilder,
    required void Function(NotifyEvent event) onEvent,
    WsConnector? connect,
    this.backoff = const ReconnectBackoff(),
    this.maxReconnectAttempts = 0,
    TimerFactory? timerFactory,
    BackoffJitter? jitter,
    this.heartbeatInterval = kHeartbeatInterval,
  })  : _uriBuilder = uriBuilder,
        _onEvent = onEvent,
        _connect = connect ?? WebSocketChannel.connect,
        _timerFactory = timerFactory ?? ((d, cb) => Timer(d, cb)),
        _customTimer = timerFactory != null,
        _jitter = jitter ?? BackoffJitter();

  final Uri Function() _uriBuilder;
  final void Function(NotifyEvent event) _onEvent;
  final WsConnector _connect;
  final ReconnectBackoff backoff;
  final int maxReconnectAttempts;
  final TimerFactory _timerFactory;

  /// 调用方自带定时器（单测 / 仿真）→ 退避不加抖动，保证时序可复现。
  final bool _customTimer;
  final BackoffJitter _jitter;

  /// 心跳间隔；`Duration.zero`（或负值）表示关闭探活。
  final Duration heartbeatInterval;

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _reconnectTimer;
  Timer? _heartbeatTimer;
  bool _disposed = false;
  int _attempt = 0;
  int _beatData = 0; // 本心跳周期内收到的数据条数（pong / 业务推送都算）
  int _beatMiss = 0; // 连续「发了 ping 也没回数据」的周期数

  void start() {
    _disposed = false;
    _attempt = 0;
    _connectNow();
  }

  void _connectNow() {
    if (_disposed) return;
    try {
      final uri = _uriBuilder();
      _channel = _connect(uri);
      _sub = _channel!.stream.listen(
        _onData,
        onError: (_) => _scheduleReconnect(),
        onDone: () => _scheduleReconnect(),
        cancelOnError: true,
      );
      _startHeartbeat();
    } catch (_) {
      // 连接建立失败（如 URI 非法）：直接进入退避重连
      _scheduleReconnect();
    }
  }

  void _onData(dynamic data) {
    if (data == null) return;
    // 收到任意数据说明连接健康，重置退避计数
    _attempt = 0;
    _beatData++;
    final event = NotifyEvent.tryParse(_tryDecode(data));
    if (event != null) _onEvent(event);
  }

  Object? _tryDecode(dynamic data) {
    if (data is String) {
      try {
        return jsonDecode(data);
      } catch (_) {
        return null;
      }
    }
    return data;
  }

  // ── S6 心跳探活 ──

  void _startHeartbeat() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _beatData = 0;
    _beatMiss = 0;
    _scheduleHeartbeat();
  }

  void _scheduleHeartbeat() {
    if (_disposed || heartbeatInterval <= Duration.zero) return;
    _heartbeatTimer = _timerFactory(heartbeatInterval, _onHeartbeat);
  }

  void _onHeartbeat() {
    _heartbeatTimer = null;
    if (_disposed || heartbeatInterval <= Duration.zero) return;
    if (!shouldPingOnHeartbeatTick(dataSinceLastTick: _beatData)) {
      // 本周期有数据（pong 或业务推送）→ 连接健康
      _beatData = 0;
      _beatMiss = 0;
      _scheduleHeartbeat();
      return;
    }
    _beatMiss++;
    if (isHeartbeatHalfOpen(missCount: _beatMiss)) {
      // 连续两轮发了 ping 都没任何数据 → 半开连接，主动断开重连
      _scheduleReconnect();
      return;
    }
    try {
      _channel?.sink.add('{"type":"ping"}');
    } catch (_) {
      _scheduleReconnect();
      return;
    }
    _scheduleHeartbeat();
  }

  void _stopHeartbeat() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _beatData = 0;
    _beatMiss = 0;
  }

  void _scheduleReconnect() {
    _stopHeartbeat();
    _reconnectTimer?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _channel = null;
    if (_disposed) return;
    if (maxReconnectAttempts > 0 && _attempt >= maxReconnectAttempts) return;
    final base = backoff.delayFor(_attempt);
    final delay = _customTimer ? base : _jitter.apply(base);
    _attempt++;
    _reconnectTimer = _timerFactory(delay, _connectNow);
  }

  void dispose() {
    _disposed = true;
    _stopHeartbeat();
    _reconnectTimer?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _channel = null;
  }
}
