import "dart:async";
import "dart:convert";
import "package:web_socket_channel/web_socket_channel.dart";

typedef MessageCallback = void Function(Map<String, dynamic> data);

class WebSocketService {
  WebSocketService({
    WebSocketChannel Function(Uri)? connector,
    Timer Function(Duration, void Function())? timerFactory,
  })  : _connector = connector ?? WebSocketChannel.connect,
        _timerFactory =
            timerFactory ?? ((delay, callback) => Timer(delay, callback));

  final WebSocketChannel Function(Uri) _connector;
  final Timer Function(Duration, void Function()) _timerFactory;
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;
  String? _baseUrl;
  int? _sessionId;
  String _token = '';
  int _reconnectAttempt = 0;
  int _generation = 0;
  bool _enabled = false;

  void connect(String baseUrl, int sessionId,
      {String token = '', MessageCallback? onMessage}) {
    // 只有显式开启/切换会话重置预算；自动重连不能经过此入口。
    disconnect();
    _baseUrl = baseUrl;
    _sessionId = sessionId;
    _token = token;
    _enabled = true;
    _openConnection(onMessage: onMessage);
  }

  void _openConnection({MessageCallback? onMessage}) {
    if (!_enabled || _baseUrl == null || _sessionId == null) return;
    final generation = ++_generation;
    _closeConnection();
    var failed = false;
    bool current() => _enabled && generation == _generation && !failed;
    void fail() {
      if (!current()) return;
      failed = true; // error/done/ready 拒绝共享本次失败，不重复消耗预算。
      _closeConnection();
      _scheduleReconnect(onMessage: onMessage);
    }

    try {
      final wsBase = _baseUrl!
          .replaceFirst("http://", "ws://")
          .replaceFirst("https://", "wss://");
      final uri = Uri.parse("$wsBase/api/v1/chat/ws/$_sessionId").replace(
        queryParameters: {if (_token.isNotEmpty) 'token': _token},
      );
      final channel = _connector(uri);
      _channel = channel;
      _subscription = channel.stream.listen(
        (data) {
          if (!current()) return;
          if (onMessage != null && data is String) {
            final json = jsonDecode(data) as Map<String, dynamic>;
            onMessage(json);
          }
        },
        onError: (Object error) => fail(),
        onDone: fail,
        cancelOnError: true,
      );
      // 返回 channel 对象不代表握手成功；旧连接的迟到 ready 也不能清零。
      unawaited(channel.ready.then<void>(
        (_) {
          if (current()) _reconnectAttempt = 0;
        },
        onError: (Object error, StackTrace stack) => fail(),
      ));
    } catch (_) {
      fail(); // 同步创建失败也纳入相同的有界重试。
    }
  }

  void _scheduleReconnect({MessageCallback? onMessage}) {
    if (!_enabled || _reconnectTimer != null || _reconnectAttempt >= 10) return;
    final delay =
        Duration(seconds: (3 * (1 << _reconnectAttempt)).clamp(3, 60));
    _reconnectAttempt++;
    final generation = _generation;
    _reconnectTimer = _timerFactory(delay, () {
      if (!_enabled || generation != _generation) return;
      _reconnectTimer = null;
      _openConnection(onMessage: onMessage);
    });
  }

  void send(Map<String, dynamic> data) {
    _channel?.sink.add(jsonEncode(data));
  }

  void _closeConnection() {
    final subscription = _subscription;
    final channel = _channel;
    _subscription = null;
    _channel = null;
    if (subscription != null) {
      unawaited(
          subscription.cancel().then<void>((_) {}, onError: (Object error) {}));
    }
    if (channel != null) {
      unawaited(
          channel.sink.close().then<void>((_) {}, onError: (Object error) {}));
    }
  }

  void disconnect() {
    _enabled = false;
    _generation++; // 先撤销旧回调资格，再取消订阅和关闭 socket。
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _closeConnection();
    _baseUrl = null;
    _sessionId = null;
    _token = '';
    _reconnectAttempt = 0;
  }
}
