import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

import 'voice_recorder_types.dart';

VoiceRecorder createPlatformVoiceRecorder() => _WebVoiceRecorder();

class _WebVoiceRecorder implements VoiceRecorder {
  web.MediaRecorder? _recorder;
  web.MediaStream? _stream;
  final List<web.Blob> _chunks = [];
  DateTime? _startedAt;

  @override
  bool get isRecording => _recorder?.state == 'recording';

  @override
  Future<bool> start() async {
    if (isRecording) return true;
    try {
      final constraints = web.MediaStreamConstraints(audio: true.toJS);
      _stream = await web.window.navigator.mediaDevices
          .getUserMedia(constraints)
          .toDart;
      final recorder = web.MediaRecorder(_stream!);
      _chunks.clear();
      recorder.ondataavailable = ((web.BlobEvent e) {
        if (e.data.size > 0) _chunks.add(e.data);
      }).toJS;
      recorder.start();
      _recorder = recorder;
      _startedAt = DateTime.now();
      return true;
    } catch (_) {
      await cancel();
      return false;
    }
  }

  @override
  Future<RecordedVoice?> stop() async {
    final recorder = _recorder;
    if (recorder == null) return null;
    final done = Completer<RecordedVoice?>();
    recorder.onstop = ((web.Event _) {
      _finishStop(done);
    }).toJS;
    try {
      recorder.stop();
    } catch (_) {
      if (!done.isCompleted) done.complete(null);
    }
    return done.future;
  }

  Future<void> _finishStop(Completer<RecordedVoice?> done) async {
    try {
      final blob = web.Blob(_chunks.toJS);
      final buffer = await blob.arrayBuffer().toDart;
      final bytes = Uint8List.view(buffer.toDart);
      final duration = _startedAt == null
          ? 0
          : DateTime.now().difference(_startedAt!).inSeconds;
      if (!done.isCompleted) {
        done.complete(bytes.isEmpty
            ? null
            : RecordedVoice(
                bytes: bytes,
                mimeType: blob.type.isEmpty ? 'audio/webm' : blob.type,
                durationSeconds: duration.clamp(0, 3600),
              ));
      }
    } catch (_) {
      if (!done.isCompleted) done.complete(null);
    } finally {
      await cancel();
    }
  }

  @override
  Future<void> cancel() async {
    try {
      _recorder?.stop();
    } catch (_) {}
    _recorder = null;
    for (final track in _stream?.getTracks().toDart ?? const []) {
      try {
        track.stop();
      } catch (_) {}
    }
    _stream = null;
    _chunks.clear();
    _startedAt = null;
  }
}
