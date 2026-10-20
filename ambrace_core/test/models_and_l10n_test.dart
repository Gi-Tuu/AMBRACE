import 'dart:convert';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('ChatMessage 共享解析（验收 1：同一 JSON 两端一致）', () {
    test('基础字段逐字解析', () {
      final json = {
        'id': 7,
        'session_id': 3,
        'sender_type': 'ai',
        'content': '你好',
        'image_url': null,
        'created_at': '2026-10-15 09:30:00',
      };
      final m = ChatMessage.fromJson(json);
      expect(m.id, 7);
      expect(m.sessionId, 3);
      expect(m.senderType, 'ai');
      expect(m.content, '你好');
      // 服务器 UTC 无时区标记 → 补 Z
      expect(m.createdAt, '2026-10-15T09:30:00Z');
      expect(m.isAI, isTrue);
      expect(m.isUser, isFalse);
    });

    test('extra_meta 字符串 JSON 解析 + file/image 合并且不崩', () {
      final json = {
        'id': 1,
        'session_id': 2,
        'sender_type': 'ai',
        'content': '文件',
        'created_at': '2026-10-15T09:30:00Z',
        'extra_meta': jsonEncode({'kind': 'ai_image'}),
        'file_url': 'http://127.0.0.1/x.png',
        'image_url': 'http://127.0.0.1/y.png',
      };
      final m = ChatMessage.fromJson(json);
      expect(m.fileMeta?['url'], 'http://127.0.0.1/x.png');
      expect(m.isAiGeneratedImage, isTrue);
      expect(m.copyWith(content: '改').content, '改');
      expect(m.copyWith(content: '改').id, 1);
    });

    test('extra_meta 内层 Map 键非 String 时防御性转换不抛异常', () {
      final json = {
        'id': 1,
        'session_id': 2,
        'sender_type': 'ai',
        'content': 'x',
        'created_at': '2026-10-15T09:30:00Z',
        'extra_meta': <String, dynamic>{
          'quote': <dynamic, dynamic>{'id': 5},
        },
      };
      final m = ChatMessage.fromJson(json);
      expect(m.quoteMeta?['id'], 5);
    });
  });

  group('Character 最小模型', () {
    test('容错解析（缺字段不崩）', () {
      final c = Character.fromJson({'id': 9, 'name': '小暖'});
      expect(c.id, 9);
      expect(c.name, '小暖');
      expect(c.avatarUrl, isNull);
      expect(Character.fromJson({'id': '11', 'name': 'a'}).id, 11);
    });
  });

  group('SSE data 行解析', () {
    test('合法事件 / 非 data 行 / 空负载 / 坏 JSON / [DONE]', () {
      expect(parseSseDataLine('data: {"type":"delta","text":"a"}')?['text'], 'a');
      expect(parseSseDataLine('event: ping'), isNull);
      expect(parseSseDataLine('data:'), isNull);
      expect(parseSseDataLine('data: {bad json'), isNull);
      expect(parseSseDataLine('data: [DONE]')?['type'], 'done');
      expect(parseSseDataLine('data: [1,2]'), isNull); // 非对象
    });
  });

  group('CoreStrings zh/en 成对', () {
    test('两端 key 集合一致且都有文案', () {
      const zh = CoreStrings('zh');
      const en = CoreStrings('en');
      const keys = [
        'appTitle', 'serverAddress', 'login', 'logout', 'username', 'password',
        'characters', 'history', 'send', 'messageHint', 'emptyCharacters',
        'emptyMessages', 'connecting', 'offline', 'unauthorized',
        'identityMismatch', 'sending', 'cancelled', 'switchAccount',
      ];
      for (final k in keys) {
        expect(zh.t(k), isNotEmpty, reason: 'zh 缺 $k');
        expect(en.t(k), isNotEmpty, reason: 'en 缺 $k');
        expect(zh.t(k), isNot(equals(k)), reason: 'zh 的 $k 未翻译');
        expect(en.t(k), isNot(equals(k)), reason: 'en 的 $k 未翻译');
      }
      expect(zh.t('login'), '登录');
      expect(en.t('login'), 'Sign in');
      // 未知 key 回落原样（不静默成功）
      expect(zh.t('__missing__'), '__missing__');
    });
  });
}
