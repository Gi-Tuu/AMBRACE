import 'dart:typed_data';

class RecordedVoice {
  const RecordedVoice({
    required this.bytes,
    required this.mimeType,
    required this.durationSeconds,
  });

  final Uint8List bytes;
  final String mimeType;
  final int durationSeconds;

  String get filename {
    if (mimeType.contains('mp4')) return 'voice.m4a';
    if (mimeType.contains('ogg')) return 'voice.ogg';
    return 'voice.webm';
  }
}

/// 浏览器语音录制器：start/stop 由壳的麦克风按钮驱动。
abstract interface class VoiceRecorder {
  bool get isRecording;
  Future<bool> start();
  Future<RecordedVoice?> stop();
  Future<void> cancel();
}
