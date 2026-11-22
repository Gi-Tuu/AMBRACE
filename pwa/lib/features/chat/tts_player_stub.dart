import 'tts_player_types.dart';

TtsPlayer createPlatformTtsPlayer() => _StubTtsPlayer();

class _StubTtsPlayer implements TtsPlayer {
  @override
  void play(String url) {}

  @override
  void stop() {}

  @override
  void dispose() {}
}
