
import 'dart:async';

import 'package:audioplayers/audioplayers.dart';

/// 抽象音频播放器：便于单测注入 fake，不依赖真实 audioplayers 平台通道。
abstract class VoiceAudioPlayer {
  /// 当前音频播放完成事件流（触发下一句）。
  Stream<void> get onComplete;

  Future<void> play(String url);

  Future<void> stop();

  Future<void> dispose();
}

/// 真实实现：包裹 audioplayers 的 [AudioPlayer]（UrlSource 播放）。
class AudioPlayersVoicePlayer implements VoiceAudioPlayer {
  final AudioPlayer _inner = AudioPlayer();

  @override
  Stream<void> get onComplete => _inner.onPlayerComplete;

  @override
  Future<void> play(String url) => _inner.play(UrlSource(url));

  @override
  Future<void> stop() => _inner.stop();

  @override
  Future<void> dispose() => _inner.dispose();
}

/// 语音逐句顺序播放队列：收到带 tts_url 的 block 依次入队播放，
/// 上一句播完播下一句（跨 onPlayerComplete 链式排队）；interrupt() 清空队列并停止当前（新语音打断）。
class VoicePlaybackQueue {
  VoicePlaybackQueue({VoiceAudioPlayer Function()? playerFactory})
      : _playerFactory = playerFactory ?? AudioPlayersVoicePlayer.new;

  final VoiceAudioPlayer Function() _playerFactory;
  final List<String> _queue = [];
  VoiceAudioPlayer? _player;
  StreamSubscription<void>? _onCompleteSub;
  bool _playing = false;
  bool _interrupted = false;

  bool get isPlaying => _playing;
  int get pendingCount => _queue.length;

  void enqueue(String url) {
    if (url.isEmpty) return;
    _queue.add(url);
    if (!_playing) _playNext();
  }

  /// 打断：清空队列、停止当前（新一轮语音/手动停止时调用）。
  void interrupt() {
    _interrupted = true;
    _queue.clear();
    _playing = false;
    _player?.stop();
  }

  VoiceAudioPlayer _ensurePlayer() {
    if (_player == null) {
      _player = _playerFactory();
      _onCompleteSub = _player!.onComplete.listen((_) {
        if (_interrupted) return;
        _playing = false;
        if (_queue.isNotEmpty) _playNext();
      });
    }
    return _player!;
  }

  void _playNext() {
    if (_queue.isEmpty) {
      _playing = false;
      return;
    }
    // V2-2：开始播放新句时重置打断标志——上一轮 interrupt() 只应打断"当时"的播放链，
    // 此后新入队的句子要能继续逐句自动播放，不能因 _interrupted 恒为 true 永久卡死。
    _interrupted = false;
    final url = _queue.removeAt(0);
    _playing = true;
    // 单句失败（网络/解码异常）跳过继续下一句，不阻塞整段播报。
    _ensurePlayer().play(url).then((_) {}, onError: (_) {
      if (_interrupted) return;
      _playing = false;
      _playNext();
    });
  }

  /// 释放播放器与订阅（Provider dispose 时调用）。
  Future<void> dispose() async {
    _interrupted = true;
    _queue.clear();
    await _onCompleteSub?.cancel();
    _onCompleteSub = null;
    await _player?.dispose();
    _player = null;
    _playing = false;
  }
}
