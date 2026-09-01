import 'dart:async';

import 'package:record/record.dart';

/// 语音通话录音抽象：便于组件测试注入 fake，不依赖 record 平台通道。
abstract class VoiceCallRecorder {
  /// 是否获得麦克风权限（请求）。
  Future<bool> hasPermission();

  /// 开始录音到 [path]（m4a/AAC，与后端 _asr_text / 语音消息链路兼容）。
  Future<void> start({required String path});

  /// 停止录音并返回文件路径（未在录音返回 null）。
  Future<String?> stop();

  /// 录音期间的实时音量流（dBFS），供 VAD 使用；未在录音时返回 null。
  Stream<Amplitude>? get amplitude;

  /// 释放资源。
  Future<void> dispose();
}

/// 真实实现：包裹 record 6.2.1 的 [AudioRecorder]。
class RecordVoiceCallRecorder implements VoiceCallRecorder {
  final AudioRecorder _inner = AudioRecorder();
  Stream<Amplitude>? _amplitude;

  @override
  Future<bool> hasPermission() => _inner.hasPermission();

  @override
  Future<void> start({required String path}) async {
    // m4a(AAC) 压缩：整段语音体积小，弱网下不易断连（与 _VoiceRecordSheet 一致）；
    // 后端 faster-whisper 可解码该格式。
    await _inner.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    _amplitude = _inner.onAmplitudeChanged(const Duration(milliseconds: 200));
  }

  @override
  Future<String?> stop() => _inner.stop();

  @override
  Stream<Amplitude>? get amplitude => _amplitude;

  @override
  Future<void> dispose() => _inner.dispose();
}

/// 生成临时 m4a 录音路径（[dir] 为目录，如 Directory.systemTemp.path）。
String newVoiceRecPath(String dir) {
  final ts = DateTime.now().millisecondsSinceEpoch;
  return '$dir/voice_$ts.m4a';
}
