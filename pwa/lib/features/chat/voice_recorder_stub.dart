import 'voice_recorder_types.dart';

VoiceRecorder createPlatformVoiceRecorder() => _StubVoiceRecorder();

class _StubVoiceRecorder implements VoiceRecorder {
  @override
  bool get isRecording => false;

  @override
  Future<bool> start() async => false;

  @override
  Future<RecordedVoice?> stop() async => null;

  @override
  Future<void> cancel() async {}
}
