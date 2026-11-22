import 'dart:async';
import 'dart:js_interop';

import 'package:web/web.dart' as web;

import 'tts_player_types.dart';

TtsPlayer createPlatformTtsPlayer() => _WebTtsPlayer();

class _WebTtsPlayer implements TtsPlayer {
  web.HTMLAudioElement? _audio;

  @override
  void play(String url) {
    stop();
    try {
      final audio = web.HTMLAudioElement()
        ..src = url
        ..autoplay = true;
      _audio = audio;
      unawaited(audio.play().toDart.catchError((_) => null));
    } catch (_) {}
  }

  @override
  void stop() {
    try {
      _audio?.pause();
      _audio?.src = '';
    } catch (_) {}
    _audio = null;
  }

  @override
  void dispose() => stop();
}
