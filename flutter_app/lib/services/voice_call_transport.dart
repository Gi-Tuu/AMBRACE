import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// 语音通话 WS 传输回调（服务端下行 JSON 帧）。
typedef VoiceFrameHandler = void Function(Map<String, dynamic> data);

/// 语音通话传输层抽象：便于组件测试注入 fake，不依赖平台 WebSocket 通道。
abstract class VoiceCallTransport {
  /// 建立连接；[onFrame] 收到服务端解析后的 JSON 帧。
  /// [onDone]/[onError] 用于断线/异常（触发重连或提示）。
  void connect(
    Uri uri, {
    VoiceFrameHandler? onFrame,
    VoidCallback? onDone,
    void Function(Object error)? onError,
  });

  /// 发送文本控制帧（session_start / barge_in / ping / session_end）。
  void sendText(Map<String, dynamic> data);

  /// 发送二进制音频帧（整段 m4a/wav 字节）。
  void sendBinary(Uint8List bytes);

  /// 关闭连接（幂等）。
  Future<void> close();

  bool get isConnected;
}

/// 真实实现：基于 web_socket_channel 的 [WebSocketChannel]。
class WebSocketVoiceCallTransport implements VoiceCallTransport {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  bool _connected = false;

  @override
  bool get isConnected => _connected;

  @override
  void connect(
    Uri uri, {
    VoiceFrameHandler? onFrame,
    VoidCallback? onDone,
    void Function(Object error)? onError,
  }) {
    // 幂等：重连前清掉上一条（已关闭也无害），避免悬挂订阅
    _sub?.cancel();
    _sub = null;
    _channel?.sink.close();
    _channel = null;

    _channel = WebSocketChannel.connect(uri);
    _connected = true;
    _sub = _channel!.stream.listen(
      (data) {
        if (data is String && onFrame != null) {
          try {
            onFrame(jsonDecode(data) as Map<String, dynamic>);
          } catch (_) {
            // 非法 JSON 帧忽略，不中断通话
          }
        }
      },
      onDone: () {
        _connected = false;
        onDone?.call();
      },
      onError: (Object error) {
        _connected = false;
        onError?.call(error);
      },
    );
  }

  @override
  void sendText(Map<String, dynamic> data) {
    _channel?.sink.add(jsonEncode(data));
  }

  @override
  void sendBinary(Uint8List bytes) {
    _channel?.sink.add(bytes);
  }

  @override
  Future<void> close() async {
    await _sub?.cancel();
    _sub = null;
    await _channel?.sink.close();
    _channel = null;
    _connected = false;
  }
}

/// 构造 WS URI：`{wsBase}/api/v1/voice/stream?token={token}`。
/// http(s) → ws(s)；token 经 query 鉴权（后端 api/voice.py 用 token 解 JWT）。
Uri buildVoiceStreamUri(String baseUrl, String token) {
  final wsBase = baseUrl
      .replaceFirst('http://', 'ws://')
      .replaceFirst('https://', 'wss://');
  final uri = Uri.parse('$wsBase/api/v1/voice/stream');
  return uri.replace(queryParameters: {
    if (token.isNotEmpty) 'token': token,
  });
}
