
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/services/api/chat_api.dart';
import 'package:ai_companion/services/voice_playback_queue.dart';

class _FakeVoicePlayer implements VoiceAudioPlayer {
  final _completeCtrl = StreamController<void>.broadcast();
  final List<String> played = [];
  @override
  Stream<void> get onComplete => _completeCtrl.stream;
  @override
  Future<void> play(String url) async {
    played.add(url);
  }
  @override
  Future<void> stop() async {}
  @override
  Future<void> dispose() async {
    await _completeCtrl.close();
  }
}

void main() {
  group('parseSseDataLine', () {
    test('解析合法 data: 行', () {
      final ev = parseSseDataLine('data: {"type":"delta","text":"你好"}');
      expect(ev, isNotNull);
      expect(ev!['type'], 'delta');
      expect(ev['text'], '你好');
    });

    test('忽略空行 / 非 data: 行 / 非法 JSON', () {
      expect(parseSseDataLine(''), isNull);
      expect(parseSseDataLine('  '), isNull);
      expect(parseSseDataLine('event: ping'), isNull);
      expect(parseSseDataLine('data: {bad json'), isNull);
      expect(parseSseDataLine('data:'), isNull);
    });

    test('兼容前后空白', () {
      final ev = parseSseDataLine('   data: {"type":"block","content":"你好。"}  ');
      expect(ev, isNotNull);
      expect(ev!['type'], 'block');
    });
  });

  group('ChatProvider.handleStreamEvent', () {
    test('delta 追加当前气泡（打字机），block 确认替换并开启下一气泡', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'delta', 'text': '你好'});
      expect(chat.messages.length, 1);
      expect(chat.messages.last.content, '你好');
      expect(chat.messages.last.isAI, isTrue);
      expect(chat.messages.last.isLocal, isTrue);

      chat.handleStreamEvent({'type': 'delta', 'text': '。'});
      expect(chat.messages.last.content, '你好。');

      // block 为完整落库块（含 id/created_at），确认替换当前流式气泡
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.last.id, 10);
      expect(chat.messages.last.isLocal, isFalse);
      expect(chat.messages.last.content, '你好。');

      // 下一 block 的 delta 开启新气泡
      chat.handleStreamEvent({'type': 'delta', 'text': '今天'});
      expect(chat.messages.last.content, '今天');
      expect(chat.messages.last.isLocal, isTrue);

      chat.handleStreamEvent({'type': 'done'});
      expect(chat.isStreaming, isFalse);
    });

    test('error 事件写入 error 且不中断（服务端已内部回退）', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'error', 'detail': '网络中断'});
      expect(chat.error, '网络中断');
    });

    test('cold_war 事件插入 system 消息', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'cold_war', 'message': 'TA 还在生闷气'});
      expect(chat.messages.any((m) => m.senderType == 'system'), isTrue);
    });
  });

  group('ChatProvider 语音逐句 block', () {
    test('block 带 tts_url 时确认气泡并入队播放', () async {
      final fake = _FakeVoicePlayer();
      final chat = ChatProvider(voicePlayback: VoicePlaybackQueue(playerFactory: () => fake));

      chat.handleStreamEvent({'type': 'delta', 'text': '你好。'});
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
        'tts_url': '/uploads/tts/a.mp3',
      });

      // 气泡确认为落库块（id=10，非本地）
      expect(chat.messages.last.id, 10);
      expect(chat.messages.last.isLocal, isFalse);
      // 音频入队并播放
      await pumpEventQueue();
      expect(fake.played, contains('/uploads/tts/a.mp3'));
    });

    test('block 确认时保留已打字的后续尾段（delta 领先于 block）', () {
      final chat = ChatProvider();

      // delta 已把多句打进同一流式气泡
      chat.handleStreamEvent({'type': 'delta', 'text': '你好。今天'});
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });

      // 首块已被确认（落库 id=10），尾段 "今天" 保留为新的流式气泡
      expect(chat.messages.any((m) => m.id == 10 && !m.isLocal), isTrue);
      expect(chat.messages.any((m) => m.isLocal && m.content == '今天'), isTrue);

      // 后续 delta 继续追加到尾段
      chat.handleStreamEvent({'type': 'delta', 'text': '天气真好。'});
      expect(chat.messages.any((m) => m.isLocal && m.content == '今天天气真好。'), isTrue);
    });
  });
}
