import "dart:async";
import "dart:convert";
import "package:web_socket_channel/web_socket_channel.dart";

typedef MessageCallback = void Function(Map<String, dynamic> data);

class WebSocketService {
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;
  String? _baseUrl;
  int? _sessionId;
  String _token = '';

  void connect(String baseUrl, int sessionId, {String token = '', MessageCallback? onMessage}) {
    // 切换会话前先断开旧连接/订阅，防止旧连接的推送串入当前会话
    disconnect();
    _baseUrl = baseUrl;
    _sessionId = sessionId;
    _token = token;

    // Convert http:// to ws:// and build WebSocket URL with sessionId + token
    final wsBase = baseUrl
        .replaceFirst("http://", "ws://")
        .replaceFirst("https://", "wss://");
    final wsUrl = "$wsBase/api/v1/chat/ws/$sessionId";
    final uri = Uri.parse(wsUrl).replace(queryParameters: {
      if (token.isNotEmpty) 'token': token,
    });

    _channel = WebSocketChannel.connect(uri);
    _subscription = _channel!.stream.listen(
      (data) {
        if (onMessage != null && data is String) {
          final json = jsonDecode(data) as Map<String, dynamic>;
          onMessage(json);
        }
      },
      onError: (error) => _scheduleReconnect(onMessage: onMessage),
      onDone: () => _scheduleReconnect(onMessage: onMessage),
    );
  }

  int _reconnectAttempt = 0;

  void _scheduleReconnect({MessageCallback? onMessage}) {
    // P1 修复（2026-08-16）：指数退避（3s 起，上限 60s）+ 最多重试 10 次，服务器不可达不再无限空转
    if (_reconnectAttempt >= 10) return;
    _reconnectTimer?.cancel();
    final delay = Duration(seconds: (3 * (1 << _reconnectAttempt)).clamp(3, 60));
    _reconnectAttempt++;
    _reconnectTimer = Timer(delay, () {
      if (_baseUrl != null && _sessionId != null) {
        connect(_baseUrl!, _sessionId!, token: _token, onMessage: onMessage);
      }
    });
  }

  void send(Map<String, dynamic> data) {
    _channel?.sink.add(jsonEncode(data));
  }

  void disconnect() {
    _reconnectTimer?.cancel();
    _subscription?.cancel();
    _channel?.sink.close();
    _channel = null;
    _reconnectAttempt = 0;
  }
}
