import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/models/ai_chat.dart';
import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/models/memory.dart';
import 'package:ai_companion/models/message.dart';
import 'package:ai_companion/models/moment.dart';

void main() {
  group('AICharacter.fromJson', () {
    test('完整字段解析', () {
      final c = AICharacter.fromJson({
        'id': 1,
        'name': '小遥',
        'personality': '温柔',
        'chat_style': '轻声细语',
        'system_prompt': '你是小遥',
        'is_active': false,
        'voice': 'female_01',
        'voice_rate': 1.2,
        'timezone_offset': 9,
      });
      expect(c.id, 1);
      expect(c.name, '小遥');
      expect(c.personality, '温柔');
      expect(c.chatStyle, '轻声细语');
      expect(c.isActive, false);
      expect(c.voice, 'female_01');
      expect(c.voiceRate, 1.2);
      expect(c.timezoneOffset, 9);
    });

    test('缺字段兜底', () {
      final c = AICharacter.fromJson({'id': 2, 'name': '阿啪'});
      expect(c.isActive, true); // 默认启用
      expect(c.voice, isNull);
      expect(c.voiceRate, isNull);
      expect(c.timezoneOffset, isNull);
      expect(c.personality, isNull);
    });
  });

  group('ChatMessage.fromJson', () {
    test('基础字段 + created_at 补 UTC 标记', () {
      final m = ChatMessage.fromJson({
        'id': 10,
        'session_id': 5,
        'sender_type': 'ai',
        'content': '你好呀',
        'created_at': '2026-08-12 10:00:00',
      });
      expect(m.id, 10);
      expect(m.sessionId, 5);
      expect(m.isAI, true);
      expect(m.isUser, false);
      expect(m.createdAt, '2026-08-12T10:00:00Z');
      expect(m.extraMeta, isEmpty);
    });

    test('extra_meta 字符串与 Map 解析', () {
      final m = ChatMessage.fromJson({
        'id': 1,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '（思考）',
        'created_at': '2026-08-12T10:00:00Z',
        'extra_meta': '{"reasoning":"正在想","tools":["识图"]}',
      });
      expect(m.reasoning, '正在想');
      expect(m.tools, ['识图']);

      final m2 = ChatMessage.fromJson({
        'id': 2,
        'session_id': 1,
        'sender_type': 'user',
        'content': 'hi',
        'created_at': '2026-08-12T10:00:00Z',
        'extra_meta': {'quote': {'content': '原消息'}},
      });
      expect(m2.quoteMeta, isNotNull);
      expect(m2.quoteMeta!['content'], '原消息');
    });

    test('file_url / voice_url 兼容', () {
      final m = ChatMessage.fromJson({
        'id': 3,
        'session_id': 1,
        'sender_type': 'user',
        'content': '文件.pdf',
        'created_at': '2026-08-12T10:00:00Z',
        'file_url': 'http://x/f.pdf',
      });
      expect(m.fileMeta, isNotNull);
      expect(m.fileMeta!['url'], 'http://x/f.pdf');
    });
  });

  group('Moment.fromJson', () {
    test('完整字段 + 嵌套评论', () {
      final mo = Moment.fromJson({
        'id': 1,
        'character_id': 3,
        'character_name': '小遥',
        'sender_type': 'ai',
        'content': '今天的天空很好看',
        'likes_count': 2,
        'likers': ['阿啪', '知心姐姐'],
        'created_at': '2026-08-12T02:00:00Z',
        'author_tz_offset': 8,
        'liked_by_me': true,
        'comments': [
          {'id': 11, 'moment_id': 1, 'sender_type': 'user', 'sender_id': 1, 'sender_name': '我', 'content': '好看'},
        ],
      });
      expect(mo.id, 1);
      expect(mo.characterName, '小遥');
      expect(mo.likers, ['阿啪', '知心姐姐']);
      expect(mo.likedByMe, true);
      expect(mo.comments, hasLength(1));
      expect(mo.comments.first.content, '好看');
    });

    test('缺字段兜底', () {
      final mo = Moment.fromJson({'id': 2, 'content': '只有内容'});
      expect(mo.characterId, 0);
      expect(mo.characterName, '');
      expect(mo.authorTzOffset, 8); // 默认北京时间
      expect(mo.likedByMe, false);
      expect(mo.comments, isEmpty);
      expect(mo.likers, isEmpty);
    });
  });

  group('AIChat.fromJson', () {
    test('num 转 int + 时间解析', () {
      final c = AIChat.fromJson({
        'id': 1,
        'character_a_id': 1,
        'character_a_name': '小遥',
        'character_b_id': 2,
        'character_b_name': '阿啪',
        'speaker_id': 1,
        'speaker_name': '小遥',
        'round_seq': 1,
        'content': '你最近怎么样',
        'created_at': '2026-08-12T02:00:00Z',
      });
      expect(c.id, 1);
      expect(c.speakerName, '小遥');
      expect(c.roundSeq, 1);
      expect(c.createdAt, isA<DateTime>());
    });
  });

  group('Memory.fromJson', () {
    test('完整字段 + 布尔/数值兜底', () {
      final mem = Memory.fromJson({
        'id': 1,
        'memory_type': 'event',
        'content': '一起看了电影',
        'importance': 80,
        'importance_pct': 96.0,
        'is_pinned': true,
        'is_locked': true,
        'why_it_matters': '重要回忆',
        'created_at': '2026-08-12T02:00:00Z',
      });
      expect(mem.memoryType, 'event');
      expect(mem.importance, 80);
      expect(mem.importancePct, 96.0);
      expect(mem.isPinned, true);
      expect(mem.whyItMatters, '重要回忆');
    });

    test('缺字段兜底', () {
      final mem = Memory.fromJson({'id': 2, 'memory_type': 'event', 'content': 'x', 'created_at': ''});
      expect(mem.importance, 1);
      expect(mem.importancePct, 0);
      expect(mem.isPinned, false);
      expect(mem.isLocked, false);
      expect(mem.whyItMatters, isNull);
    });
  });
}
