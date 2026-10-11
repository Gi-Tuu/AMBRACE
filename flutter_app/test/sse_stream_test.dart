
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
    test('整块上屏（§10）：delta 不渲染，block 到达才落一条气泡', () {
      final chat = ChatProvider();
      // §10（2026-10-11 用户拍板）：逐字流式取消——delta 只被消费，不生成任何气泡
      chat.handleStreamEvent({'type': 'delta', 'text': '你好'});
      chat.handleStreamEvent({'type': 'delta', 'text': '。'});
      expect(chat.messages.where((m) => m.isAI).length, 0,
          reason: 'delta 还造出气泡＝逐字流式没关掉');

      // block 是完整落库块（含 id/created_at）：整块一次性上屏
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

      // §10：后续 delta 文本不再上屏——既不新增本地气泡，也不改已确认块的正文
      final before = chat.messages.length;
      chat.handleStreamEvent({'type': 'delta', 'text': '今天'});
      expect(chat.messages.length, before, reason: 'delta 又造出气泡＝逐字流式没关掉');
      expect(chat.messages.last.id, 10, reason: 'delta 又改写了已确认块的正文');

      // §10：光标留到第一个 block 才熄（delta 不清 typing）
      chat.handleStreamEvent({'type': 'typing', 'is_typing': true});
      chat.handleStreamEvent({'type': 'delta', 'text': '半句'});
      expect(chat.isTyping, isTrue, reason: 'delta 提前收光标＝中间出现"没字也没光标"的空窗');
      chat.handleStreamEvent({
        'type': 'block', 'id': 11, 'session_id': 1, 'sender_type': 'ai',
        'content': '半句', 'created_at': '2026-08-24T00:00:01Z', 'extra_meta': null,
      });
      expect(chat.isTyping, isFalse, reason: '整块已上屏，光标必须收掉');
      expect(chat.messages.last.content, '半句');

      chat.handleStreamEvent({'type': 'done'});
      expect(chat.isStreaming, isFalse);
    });

    test('tool_result 先攒在本轮，block 确认时附到正式块（§10：不再凭空造气泡）', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({
        'type': 'tool_result',
        'tool': 'mcp.srv.echo',
        'ok': true,
        'summary': 'echo: hi',
      });
      // §10 之后没有"流式气泡"可附 ⇒ 工具结果到达时**不新增任何气泡**
      expect(chat.messages.where((m) => m.isAI).length, 0,
          reason: '为一条工具结果凭空造个气泡＝屏幕上出现没有正文的假回复');

      // block 确认后，工具结果随正式块保留（不因块替换丢失）
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '查一下。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.last.id, 10);
      expect(chat.messages.last.toolResults, isNotNull);
      expect(chat.messages.last.toolResults!.single['tool'], 'mcp.srv.echo');
    });

    test('回退批量路径重推已确认块：按块 id 去重，不重复添加', () {
      final chat = ChatProvider();
      // 首次确认块 id=10（_streamingMessage 为 null 时走 else 分支添加）
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.where((m) => m.id == 10 && !m.isLocal).length, 1);

      // P3-5：服务端回退批量路径全量重推同一块（后端 done.fallback=true 场景），按 id 去重
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.where((m) => m.id == 10 && !m.isLocal).length, 1);
    });

    test('reset_blocks 移除本轮 AI 正式块并重置流式气泡', () {
      final chat = ChatProvider();
      // 已确认的 AI 正式块（id>0，非本地）
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.where((m) => m.isAI && !m.isLocal && m.id > 0).length, 1);

      // P2-NEW：回退批量路径先发 reset_blocks → 只清除本轮 AI 正式块
      chat.handleStreamEvent({'type': 'reset_blocks', 'reason': 'tts_consumer_fallback'});
      expect(chat.messages.where((m) => m.isAI && !m.isLocal && m.id > 0).length, 0);

      // §10：delta 不再渲染任何内容（流式气泡这个载体已移除）
      chat.handleStreamEvent({'type': 'delta', 'text': '新的句子。'});
      expect(chat.messages.where((m) => m.isAI).length, 0,
          reason: 'delta 又上屏＝逐字流式没关干净');
    });

    test('reset_blocks 保留非本轮消息（用户/system 消息不受影响）', () {
      final chat = ChatProvider();
      // 一条用户正式消息（isLocal=false，senderType=user；user_message 载荷位于 data 字段）
      chat.handleStreamEvent({
        'type': 'user_message',
        'data': {
          'id': 5,
          'session_id': 1,
          'sender_type': 'user',
          'content': '你好',
          'created_at': '2026-08-24T00:00:00Z',
          'extra_meta': null,
        },
      });
      // 新增一条 AI 正式块
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '回答。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });
      expect(chat.messages.any((m) => m.senderType == 'user' && m.id == 5), isTrue);
      expect(chat.messages.any((m) => m.isAI && m.id == 10 && !m.isLocal), isTrue);

      chat.handleStreamEvent({'type': 'reset_blocks', 'reason': 'tts_consumer_fallback'});
      // AI 正式块被移除，用户消息保留
      expect(chat.messages.any((m) => m.isAI && m.id == 10), isFalse);
      expect(chat.messages.any((m) => m.senderType == 'user' && m.id == 5), isTrue);
    });

    test('reset_blocks 多轮历史场景：仅清除本轮块，前几轮 AI 消息保留', () {
      final chat = ChatProvider();
      // 第 1 轮：确认 AI 正式块 id=10，随后 done（本轮结束，_streamingBlockIds 清空）
      chat.handleStreamEvent({
        'type': 'block', 'id': 10, 'session_id': 1, 'sender_type': 'ai',
        'content': '第一轮回答。', 'created_at': '2026-08-24T00:00:00Z', 'extra_meta': null,
      });
      chat.handleStreamEvent({'type': 'done'});
      // 第 2 轮：确认 AI 正式块 id=11，随后 done
      chat.handleStreamEvent({
        'type': 'block', 'id': 11, 'session_id': 1, 'sender_type': 'ai',
        'content': '第二轮回答。', 'created_at': '2026-08-24T00:00:01Z', 'extra_meta': null,
      });
      chat.handleStreamEvent({'type': 'done'});
      // 第 3 轮（当前轮）：确认 AI 正式块 id=20 + 流式进行中的本地气泡
      chat.handleStreamEvent({
        'type': 'block', 'id': 20, 'session_id': 1, 'sender_type': 'ai',
        'content': '第三轮回答。', 'created_at': '2026-08-24T00:00:02Z', 'extra_meta': null,
      });
      chat.handleStreamEvent({'type': 'delta', 'text': '本轮新句。'});
      // 三轮回合的三块 AI 消息都在
      expect(chat.messages.where((m) => m.isAI && !m.isLocal && m.id > 0).length, 3);
      expect(chat.messages.any((m) => m.id == 10 && m.isAI && !m.isLocal), isTrue);
      expect(chat.messages.any((m) => m.id == 11 && m.isAI && !m.isLocal), isTrue);
      expect(chat.messages.any((m) => m.id == 20 && m.isAI && !m.isLocal), isTrue);

      // 第 3 轮 TTS 回退 → reset_blocks：只清本轮块 20，历史 10/11 保留
      chat.handleStreamEvent({'type': 'reset_blocks', 'reason': 'tts_consumer_fallback'});
      expect(chat.messages.any((m) => m.id == 20), isFalse);
      expect(chat.messages.any((m) => m.id == 10 && m.isAI && !m.isLocal), isTrue);
      expect(chat.messages.any((m) => m.id == 11 && m.isAI && !m.isLocal), isTrue);
    });

    test('error 事件写入 error 且不中断（服务端已内部回退）', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'error', 'detail': '网络中断'});
      expect(chat.error, '网络中断');
    });

    test('error 事件清除输入中状态（P2-2）', () {
      final chat = ChatProvider();
      chat.handleStreamEvent({'type': 'typing', 'is_typing': true});
      expect(chat.isTyping, isTrue);
      chat.handleStreamEvent({'type': 'error', 'detail': '网络中断'});
      expect(chat.error, '网络中断');
      expect(chat.isTyping, isFalse);
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

    test('delta 领先于块边界不再搬尾段（§10：流式气泡这个载体已移除）', () {
      final chat = ChatProvider();

      // 旧形状：delta 先打到「你好。今天」，block 只确认「你好。」⇒ 尾段"今天"要保住不闪没。
      // 现在没有中间态可保：delta 完全不渲染，屏幕上等 block。
      chat.handleStreamEvent({'type': 'delta', 'text': '你好。今天'});
      expect(chat.messages.where((m) => m.isAI).length, 0);
      chat.handleStreamEvent({
        'type': 'block',
        'id': 10,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '你好。',
        'created_at': '2026-08-24T00:00:00Z',
        'extra_meta': null,
      });

      // 只上屏已落库的那一块；不会因为"领先半句"再多出半个气泡
      expect(chat.messages.where((m) => m.id == 10 && !m.isLocal).length, 1);
      expect(chat.messages.any((m) => m.isLocal), isFalse,
          reason: '又冒出本地占位气泡＝流式气泡没摘干净');

      // 下一句仍等它自己的 block
      chat.handleStreamEvent({'type': 'delta', 'text': '天气真好。'});
      expect(chat.messages.where((m) => m.isAI).length, 1);
      chat.handleStreamEvent({
        'type': 'block',
        'id': 11,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '天气真好。',
        'created_at': '2026-08-24T00:00:01Z',
        'extra_meta': null,
      });
      expect(chat.messages.where((m) => m.isAI && !m.isLocal).length, 2);
    });
  });
}
