import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../models/character.dart';
import '../../services/voice_call_recorder.dart';
import '../../services/voice_call_transport.dart';
import '../../services/voice_playback_queue.dart';
import '../../services/voice_vad.dart';
import '../../widgets/ai_avatar.dart';
import '../../l10n/app_localizations.dart';

/// 通话状态机（驱动 UI 状态文案与交互）。
enum VoiceCallStatus {
  connecting,
  ready,
  recording,
  speaking,
  interrupted,
  disconnected,
  error,
  ended,
}

/// 通话界面的数组项：用户/ AI 文本（asr_final / llm_sentence）。
class _TranscriptItem {
  _TranscriptItem({required this.fromAi, required this.text});
  final bool fromAi;
  final String text;
}

/// 电话式语音通话界面（Phase 1，基于既有 WS /api/v1/voice/stream）。
///
/// 交互：
/// - 默认「按住说话」：长按麦克风录音、松手上传整段（后端整段 ASR，一条二进制帧）；
/// - 可选「自动聆听(VAD)」：开关打开后点按麦克风进入连续监听，能量端点检测说话起止，
///   自动分段提交（能量型 VAD，非 Silero，见 voice_vad.dart 说明）；
/// - 打断：按住说话（新录音开始）或「打断」按钮 → 停止播放 + 发 {"type":"barge_in"}；
/// - 挂断：关闭 WS + 停止录音 + 停止播放；断线自动重连 1-2 次后提示。
class VoiceCallScreen extends StatefulWidget {
  const VoiceCallScreen({
    super.key,
    required this.baseUrl,
    required this.token,
    required this.sessionId,
    required this.character,
    this.transport,
    this.recorder,
    this.playbackQueue,
    this.maxReconnectAttempts = 2,
  });

  final String baseUrl;
  final String token;
  final int sessionId;
  final AICharacter character;

  /// 测试注入点：默认为真实实现。
  final VoiceCallTransport? transport;
  final VoiceCallRecorder? recorder;
  final VoicePlaybackQueue? playbackQueue;

  /// 断线后最大重连次数（0=不重连直接提示）。
  final int maxReconnectAttempts;

  @override
  State<VoiceCallScreen> createState() => _VoiceCallScreenState();
}

class _VoiceCallScreenState extends State<VoiceCallScreen> {
  late VoiceCallTransport _transport;
  late VoiceCallRecorder _recorder;
  late VoicePlaybackQueue _playback;

  VoiceCallStatus _status = VoiceCallStatus.connecting;
  final List<_TranscriptItem> _transcript = [];
  String _errorMessage = '';
  String _notice = '';
  bool _thinkingQueued = false;

  // 重连
  int _reconnectAttempts = 0;
  Timer? _reconnectTimer;
  bool _ended = false;

  // 录音 / 一段
  String? _recPath;
  bool _recording = false;

  // VAD（自动聆听）
  bool _autoVad = false;
  bool _autoListening = false;
  StreamSubscription<dynamic>? _amplitudeSub;
  final VadGate _vadGate = VadGate();

  // 计时器（录音时长显示）
  Timer? _recTimer;
  int _recSeconds = 0;

  @override
  void initState() {
    super.initState();
    _transport = widget.transport ?? WebSocketVoiceCallTransport();
    _recorder = widget.recorder ?? RecordVoiceCallRecorder();
    _playback = widget.playbackQueue ?? VoicePlaybackQueue();
    _connect();
  }

  @override
  void dispose() {
    _ended = true;
    _reconnectTimer?.cancel();
    _recTimer?.cancel();
    _amplitudeSub?.cancel();
    _recorder.dispose();
    _playback.dispose();
    _transport.close();
    super.dispose();
  }

  // ── 连接 ─────────────────────────────────────────────────────

  void _connect() {
    if (_ended) return;
    setState(() => _status = VoiceCallStatus.connecting);
    final uri = buildVoiceStreamUri(widget.baseUrl, widget.token);
    _transport.connect(
      uri,
      onFrame: _onFrame,
      onDone: _onTransportClosed,
      onError: (_) => _onTransportClosed(),
    );
    _sendSessionStart();
  }

  void _sendSessionStart() {
    _transport.sendText({
      'type': 'session_start',
      'session_id': widget.sessionId,
      'character_id': widget.character.id,
    });
  }

  void _scheduleReconnect() {
    if (_ended) return;
    if (_reconnectAttempts >= widget.maxReconnectAttempts) {
      setState(() {
        _status = VoiceCallStatus.disconnected;
      });
      return;
    }
    final attempt = _reconnectAttempts;
    _reconnectAttempts++;
    _reconnectTimer?.cancel();
    // 1~2s 退避
    _reconnectTimer = Timer(Duration(seconds: attempt + 1), () {
      if (!_ended) _connect();
    });
  }

  void _onTransportClosed() {
    if (_ended) return;
    _playback.interrupt();
    _scheduleReconnect();
  }

  // ── 服务端帧 ─────────────────────────────────────────────────

  void _onFrame(Map<String, dynamic> data) {
    if (_ended || !mounted) return;
    final type = data['type'] as String?;
    switch (type) {
      case 'ready':
        final ok = data['ok'] as bool? ?? false;
        if (ok) {
          setState(() => _status = VoiceCallStatus.ready);
        } else {
          setState(() {
            _status = VoiceCallStatus.error;
            _errorMessage = l10n.voiceCallFailed;
          });
        }
        break;

      case 'asr_final':
        final text = (data['text'] as String? ?? '').trim();
        if (text.isNotEmpty) {
          setState(() {
            _transcript.add(_TranscriptItem(fromAi: false, text: text));
            _status = VoiceCallStatus.ready;
          });
        } else {
          _showNotice(l10n.voiceNotHeard);
        }
        break;

      case 'ai_thinking':
        final url = data['url'] as String? ?? '';
        if (url.isNotEmpty && !_thinkingQueued) {
          _thinkingQueued = true;
          _playback.enqueue(_resolveUrl(url));
        }
        break;

      case 'ai_speaking_start':
        setState(() => _status = VoiceCallStatus.speaking);
        break;

      case 'llm_sentence':
        final text = (data['text'] as String? ?? '').trim();
        if (text.isNotEmpty) {
          setState(() {
            _transcript.add(_TranscriptItem(fromAi: true, text: text));
            _status = VoiceCallStatus.speaking;
          });
        }
        break;

      case 'tts_audio':
        final url = data['url'] as String? ?? '';
        if (url.isNotEmpty) {
          setState(() => _status = VoiceCallStatus.speaking);
          _playback.enqueue(_resolveUrl(url));
        }
        break;

      case 'ai_speaking_end':
        setState(() => _status = VoiceCallStatus.ready);
        break;

      case 'ai_interrupted':
        _playback.interrupt();
        setState(() {
          _status = VoiceCallStatus.ready;
          _notice = l10n.voiceBargeInHint;
        });
        break;

      case 'error':
        setState(() {
          _status = VoiceCallStatus.error;
          _errorMessage = data['message'] as String? ?? l10n.voiceCallFailed;
        });
        break;

      default:
        break;
    }
  }

  // ── 打断 / 播放 ──────────────────────────────────────────────

  String _resolveUrl(String url) {
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    return '${widget.baseUrl.replaceAll(RegExp(r'/+$'), '')}$url';
  }

  void _bargeIn() {
    _playback.interrupt();
    _thinkingQueued = false; // 新一轮可再次播放思考音
    _transport.sendText({'type': 'barge_in'});
    setState(() {
      _status = VoiceCallStatus.ready;
      _notice = l10n.voiceBargeInHint;
    });
  }

  // ── 按住说话 ─────────────────────────────────────────────────

  Future<void> _startHoldToTalk() async {
    if (_recording) return;
    // 开始新一轮语音 = 打断 AI 播放
    if (_playback.isPlaying || _status == VoiceCallStatus.speaking) {
      _bargeIn();
    }
    final perm = await _recorder.hasPermission();
    if (!perm) {
      _showNotice(l10n.voiceMicPermission);
      return;
    }
    final path = newVoiceRecPath(Directory.systemTemp.path);
    await _recorder.start(path: path);
    _recPath = path;
    _recording = true;
    _recSeconds = 0;
    _recTimer?.cancel();
    _recTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_recording) setState(() => _recSeconds++);
    });
    if (mounted) setState(() => _status = VoiceCallStatus.recording);
  }

  Future<void> _stopHoldToTalkAndSend() async {
    if (!_recording) return;
    _recTimer?.cancel();
    _recording = false;
    final path = _recPath;
    _recPath = null;
    String? finalPath;
    try {
      finalPath = await _recorder.stop();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _status = VoiceCallStatus.ready;
        _recSeconds = 0;
      });
    }
    await _sendAudioFile(finalPath ?? path);
  }

  // ── 自动聆听 (VAD) ───────────────────────────────────────────

  void _toggleAutoVad() {
    setState(() => _autoVad = !_autoVad);
    if (!_autoVad && _autoListening) {
      _stopAutoListen(sendPending: true);
    } else if (_autoVad && !_autoListening) {
      _startAutoListen();
    }
  }

  Future<void> _startAutoListen() async {
    if (_autoListening) return;
    // 若 AI 正播放，先打断再进入连续监听。
    if (_playback.isPlaying || _status == VoiceCallStatus.speaking) {
      _bargeIn();
    }
    final perm = await _recorder.hasPermission();
    if (!perm) {
      _showNotice(l10n.voiceMicPermission);
      setState(() => _autoVad = false);
      return;
    }
    _vadGate.reset();
    await _beginAutoRec();
    _autoListening = true;
    _subscribeAmplitude();
    if (mounted) {
      setState(() {
        _status = VoiceCallStatus.recording;
        _notice = l10n.voiceVadOn;
      });
    }
  }

  Future<void> _beginAutoRec() async {
    final path = newVoiceRecPath(Directory.systemTemp.path);
    _recPath = path;
    await _recorder.start(path: path);
  }

  void _subscribeAmplitude() {
    _amplitudeSub?.cancel();
    final stream = _recorder.amplitude;
    if (stream != null) {
      _amplitudeSub = stream.listen((amp) => _feedVad(amp.current));
    }
  }

  void _feedVad(double db) {
    if (!_autoListening) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    if (_vadGate.feed(db, now).isSpeechEnd) {
      _finalizeAndResend();
    }
  }

  Future<void> _finalizeAndResend() async {
    _autoListening = false;
    _amplitudeSub?.cancel();
    final path = _recPath;
    _recPath = null;
    try {
      final stopped = await _recorder.stop();
      await _sendAudioFile(stopped ?? path);
    } catch (_) {}
    if (_autoVad && !_ended && mounted) {
      _vadGate.reset();
      await _beginAutoRec();
      _autoListening = true;
      _subscribeAmplitude();
    }
  }

  Future<void> _stopAutoListen({required bool sendPending}) async {
    if (!_autoListening) return;
    _autoListening = false;
    _amplitudeSub?.cancel();
    final evt = _vadGate.forceEnd(DateTime.now().millisecondsSinceEpoch);
    final path = _recPath;
    _recPath = null;
    try {
      final stopped = await _recorder.stop();
      if (sendPending && evt != null) {
        await _sendAudioFile(stopped ?? path);
      }
    } catch (_) {}
    if (mounted) setState(() => _status = VoiceCallStatus.ready);
  }

  // ── 发送音频段 ───────────────────────────────────────────────

  Future<void> _sendAudioFile(String? path) async {
    if (path == null || path.isEmpty) return;
    final f = File(path);
    if (!f.existsSync()) return;
    try {
      final bytes = await f.readAsBytes();
      if (bytes.isNotEmpty) {
        _thinkingQueued = false; // 新一轮语音开始 → 本轮的思考音可再次播放
        _transport.sendBinary(Uint8List.fromList(bytes));
      }
    } catch (_) {
      // 读文件失败：忽略，不阻塞通话
    } finally {
      try {
        if (f.existsSync()) f.deleteSync();
      } catch (_) {}
    }
  }

  // ── 挂断 ─────────────────────────────────────────────────────

  void _hangup() {
    if (_ended) return;
    _ended = true;
    _reconnectTimer?.cancel();
    _recTimer?.cancel();
    _amplitudeSub?.cancel();
    _playback.interrupt();
    _transport.sendText({'type': 'session_end'});
    _transport.close();
    setState(() => _status = VoiceCallStatus.ended);
    Navigator.of(context).maybePop();
  }

  void _showNotice(String msg) {
    if (mounted) {
      setState(() {
        _notice = msg;
        if (_status == VoiceCallStatus.speaking) _status = VoiceCallStatus.ready;
      });
    }
  }

  AppLocalizations get l10n => AppLocalizations.of(context)!;

  // ── UI ───────────────────────────────────────────────────────

  String get _statusText {
    switch (_status) {
      case VoiceCallStatus.connecting:
        return l10n.voiceCalling;
      case VoiceCallStatus.ready:
        return l10n.voiceCallReady;
      case VoiceCallStatus.recording:
        return '${l10n.voiceRecording} ${_recSeconds}s';
      case VoiceCallStatus.speaking:
        return l10n.voiceSpeaking;
      case VoiceCallStatus.interrupted:
        return l10n.voiceBargeInHint;
      case VoiceCallStatus.disconnected:
        return l10n.voiceDisconnected;
      case VoiceCallStatus.error:
        return _errorMessage.isNotEmpty ? _errorMessage : l10n.voiceCallFailed;
      case VoiceCallStatus.ended:
        return l10n.voiceEndCall;
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Container(
        decoration: BoxDecoration(
          gradient: isDark
              ? const LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0xFF1B1030), Color(0xFF0D0A1F)],
                )
              : const LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0xFFF2ECFF), Color(0xFFFDFBFF)],
                ),
        ),
        child: SafeArea(
          child: Column(
            children: [
              const SizedBox(height: 12),
              // 顶栏：挂断 + 标题
              Row(
                children: [
                  IconButton(
                    icon: Icon(Icons.call_end, color: scheme.error),
                    onPressed: _hangup,
                  ),
                  Expanded(
                    child: Text(
                      l10n.voiceCallTitle,
                      textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                    ),
                  ),
                  const SizedBox(width: 48), // 对称
                ],
              ),
              const Spacer(),
              // 头像 + 角色名 + 状态
              AIAvatar(
                name: widget.character.name,
                size: 108,
                imageUrl: widget.character.avatarUrl,
              ),
              const SizedBox(height: 16),
              Text(
                widget.character.name,
                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 200),
                child: Text(
                  _statusText,
                  key: ValueKey(_status),
                  style: TextStyle(
                    fontSize: 15,
                    color: _status == VoiceCallStatus.error
                        ? scheme.error
                        : (_status == VoiceCallStatus.recording
                            ? scheme.primary
                            : Colors.grey),
                  ),
                ),
              ),
              if (_notice.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    _notice,
                    style: const TextStyle(fontSize: 13, color: Colors.orange),
                  ),
                ),
              const Spacer(),
              // 字幕区
              if (_transcript.isNotEmpty)
                Flexible(
                  child: Container(
                    width: double.infinity,
                    constraints: const BoxConstraints(maxHeight: 180),
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: ListView.builder(
                      shrinkWrap: true,
                      itemCount: _transcript.length,
                      itemBuilder: (c, i) => _transcriptBubble(_transcript[i]),
                    ),
                  ),
                ),
              const Spacer(),
              // 底部控制
              _buildControls(scheme),
              const SizedBox(height: 28),
            ],
          ),
        ),
      ),
    );
  }

  Widget _transcriptBubble(_TranscriptItem item) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Align(
        alignment: item.fromAi ? Alignment.centerLeft : Alignment.centerRight,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 260),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: item.fromAi
                ? Colors.white.withValues(alpha: 0.9)
                : Theme.of(context).colorScheme.primary.withValues(alpha: 0.85),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(
            item.text,
            style: TextStyle(
              fontSize: 14,
              color: item.fromAi ? Colors.black87 : Colors.white,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildControls(ColorScheme scheme) {
    // 打断 / VAD 切换 / 挂断 一行
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // 打断
            _controlButton(
              key: const Key('voiceInterruptButton'),
              icon: Icons.stop_circle_outlined,
              label: l10n.voiceInterrupt,
              color: scheme.tertiary,
              onTap: _bargeIn,
            ),
            const SizedBox(width: 20),
            // 挂断
            _controlButton(
              key: const Key('voiceHangupButton'),
              icon: Icons.call_end,
              label: l10n.voiceEndCall,
              color: scheme.error,
              onTap: _hangup,
            ),
            const SizedBox(width: 20),
            // VAD 自动聆听切换
            _controlButton(
              key: const Key('voiceVadToggle'),
              icon: _autoVad ? Icons.hearing : Icons.record_voice_over_outlined,
              label: _autoVad ? l10n.voiceVadOn : l10n.voiceVadOff,
              color: _autoVad ? scheme.primary : scheme.outline,
              onTap: _toggleAutoVad,
            ),
          ],
        ),
        const SizedBox(height: 18),
        // 麦克风：按住说话 / VAD 点按进入聆听
        _micButton(scheme),
        const SizedBox(height: 8),
        Text(
          _autoVad ? l10n.voiceVadOn : l10n.voiceHoldToTalk,
          style: const TextStyle(fontSize: 12, color: Colors.grey),
        ),
      ],
    );
  }

  Widget _controlButton({
    required Key? key,
    required IconData icon,
    required String label,
    required Color color,
    required VoidCallback onTap,
  }) {
    return Column(
      key: key,
      mainAxisSize: MainAxisSize.min,
      children: [
        InkResponse(
          onTap: onTap,
          radius: 28,
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.15),
              shape: BoxShape.circle,
            ),
            child: Icon(icon, color: color, size: 26),
          ),
        ),
        const SizedBox(height: 4),
        Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
      ],
    );
  }

  Widget _micButton(ColorScheme scheme) {
    final recording = _status == VoiceCallStatus.recording;
    final micColor = recording ? Colors.red.shade400 : scheme.primary;

    final Widget circle = AnimatedContainer(
      duration: const Duration(milliseconds: 120),
      width: 84,
      height: 84,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: micColor,
        boxShadow: recording
            ? [BoxShadow(color: Colors.red.withValues(alpha: 0.35), blurRadius: 20, spreadRadius: 4)]
            : null,
      ),
      child: Icon(recording ? Icons.graphic_eq : Icons.mic, color: Colors.white, size: 36),
    );

    if (_autoVad) {
      // VAD 模式：点击进入/退出自动聆听
      return InkResponse(
        key: const Key('voiceMicButton'),
        onTap: () {
          if (_autoListening) {
            _stopAutoListen(sendPending: true);
          } else {
            _startAutoListen();
          }
        },
        radius: 60,
        child: circle,
      );
    }

    // 按住说话：长按录音，松手发送
    return GestureDetector(
      key: const Key('voiceMicButton'),
      onLongPressStart: (_) => _startHoldToTalk(),
      onLongPressEnd: (_) => _stopHoldToTalkAndSend(),
      onLongPressCancel: () => _stopHoldToTalkAndSend(),
      child: circle,
    );
  }
}
