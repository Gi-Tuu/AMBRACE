
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/voice_playback_queue.dart';

/// 单测 fake：不发真实平台通道，手动触发 onComplete。
class FakeVoicePlayer implements VoiceAudioPlayer {
  final _completeCtrl = StreamController<void>.broadcast();
  final List<String> played = [];
  bool stopped = false;
  bool disposed = false;
  int? failOn; // 第 N 次（0-based）play 抛异常

  int _playCount = 0;

  @override
  Stream<void> get onComplete => _completeCtrl.stream;

  @override
  Future<void> play(String url) async {
    played.add(url);
    final idx = _playCount++;
    if (failOn == idx) {
      throw Exception('play failed: $url');
    }
  }

  @override
  Future<void> stop() async {
    stopped = true;
  }

  @override
  Future<void> dispose() async {
    disposed = true;
    await _completeCtrl.close();
  }

  void completeCurrent() {
    _completeCtrl.add(null);
  }
}

void main() {
  test('逐句顺序播放：上一句播完播下一句', () async {
    final fake = FakeVoicePlayer();
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('/uploads/tts/a.mp3');
    q.enqueue('/uploads/tts/b.mp3');
    // 只播头句，第二句等待
    expect(fake.played, ['/uploads/tts/a.mp3']);
    expect(q.isPlaying, isTrue);
    expect(q.pendingCount, 1);

    fake.completeCurrent();
    await pumpEventQueue();
    expect(fake.played, ['/uploads/tts/a.mp3', '/uploads/tts/b.mp3']);
  });

  test('播完整段后 isPlaying=false', () async {
    final fake = FakeVoicePlayer();
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('/uploads/tts/a.mp3');
    fake.completeCurrent();
    await pumpEventQueue();
    expect(fake.played, ['/uploads/tts/a.mp3']);
    expect(q.isPlaying, isFalse);
    expect(q.pendingCount, 0);
  });

  test('interrupt 清空队列并停止当前（新语音打断）', () async {
    final fake = FakeVoicePlayer();
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('/uploads/tts/a.mp3');
    q.enqueue('/uploads/tts/b.mp3');
    expect(fake.played, ['/uploads/tts/a.mp3']);

    q.interrupt();
    await pumpEventQueue();
    expect(fake.stopped, isTrue);
    expect(q.isPlaying, isFalse);
    expect(q.pendingCount, 0);

    // 打断后再次入队不会自动触发（不会播已被清空的旧句）
    fake.completeCurrent();
    await pumpEventQueue();
    expect(fake.played, ['/uploads/tts/a.mp3']);
  });

  test('interrupt 后新入队多句逐句播放（_interrupted 被重置，不卡死）', () async {
    final fake = FakeVoicePlayer();
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('/uploads/tts/a.mp3');
    q.enqueue('/uploads/tts/b.mp3');
    expect(fake.played, ['/uploads/tts/a.mp3']);

    // 打断：清空队列并停止当前
    q.interrupt();
    await pumpEventQueue();
    expect(fake.stopped, isTrue);
    expect(q.isPlaying, isFalse);
    expect(q.pendingCount, 0);

    // 打断后再次入队多句：c 立即播放，d 等待
    q.enqueue('/uploads/tts/c.mp3');
    q.enqueue('/uploads/tts/d.mp3');
    expect(fake.played, contains('/uploads/tts/c.mp3'));
    expect(fake.played, isNot(contains('/uploads/tts/d.mp3')));

    // c 播完后 d 自动播放（V2-2 修复：新播放链 _interrupted 已重置，不会卡死）
    fake.completeCurrent();
    await pumpEventQueue();
    expect(fake.played, contains('/uploads/tts/d.mp3'));

    // d 播完后队列播空，isPlaying=false
    fake.completeCurrent();
    await pumpEventQueue();
    expect(q.isPlaying, isFalse);
    expect(q.pendingCount, 0);
  });

  test('单句播放失败跳过继续下一句', () async {
    final fake = FakeVoicePlayer()..failOn = 0;
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('/uploads/tts/a.mp3');
    q.enqueue('/uploads/tts/b.mp3');
    await pumpEventQueue();
    expect(fake.played, ['/uploads/tts/a.mp3', '/uploads/tts/b.mp3']);
    expect(q.isPlaying, isTrue);
  });

  test('空 url 忽略；dispose 释放播放器', () async {
    final fake = FakeVoicePlayer();
    final q = VoicePlaybackQueue(playerFactory: () => fake);

    q.enqueue('');
    expect(fake.played, isEmpty);

    q.enqueue('/uploads/tts/a.mp3');
    await q.dispose();
    expect(fake.disposed, isTrue);
  });
}
