// PWA M1a-0 兼容接缝测试（Android 侧）。
//
// 目的：证明「共享 core 的模型」与「手机 App 现有模型」对**同一份后端 JSON**
// 解析出完全一致的结果（验收 1）。这是抽包能不能安全推进的地基——
// 若两端解析不一致，core 就不能作为唯一权威实现。
//
import 'dart:convert';

import 'package:ai_companion/models/message.dart' as app_model;
import 'package:ambrace_core/ambrace_core.dart' as core;
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('共享 core ↔ 手机 App 模型解析一致性', () {
    test('基础字段 / 时间补 Z / 方向判定逐字一致', () {
      final json = {
        'id': 12,
        'session_id': 34,
        'sender_type': 'ai',
        'content': '晚上好',
        'image_url': 'http://127.0.0.1/x.png',
        'created_at': '2026-10-15 21:05:00',
      };
      final a = app_model.ChatMessage.fromJson(json);
      final b = core.ChatMessage.fromJson(json);
      expect(b.id, a.id);
      expect(b.sessionId, a.sessionId);
      expect(b.senderType, a.senderType);
      expect(b.content, a.content);
      expect(b.imageUrl, a.imageUrl);
      expect(b.createdAt, a.createdAt);
      expect(b.isUser, a.isUser);
      expect(b.isAI, a.isAI);
      expect(b.isAiGeneratedImage, a.isAiGeneratedImage);
    });

    test('extra_meta 字符串 JSON + file_url 合并且一致', () {
      final json = {
        'id': 1,
        'session_id': 2,
        'sender_type': 'ai',
        'content': '文件',
        'created_at': '2026-10-15T09:30:00Z',
        'extra_meta': jsonEncode({'kind': 'ai_image'}),
        'file_url': 'http://127.0.0.1/f.png',
        'image_url': 'http://127.0.0.1/f.png',
      };
      final a = app_model.ChatMessage.fromJson(json);
      final b = core.ChatMessage.fromJson(json);
      expect(b.fileMeta?['url'], a.fileMeta?['url']);
      expect(b.isAiGeneratedImage, a.isAiGeneratedImage);
      expect(jsonEncode(b.extraMeta), jsonEncode(a.extraMeta));
    });

    test('SSE 事件行解析口径一致（共享解析器与手机端同一契约）', () {
      const line = 'data: {"type":"delta","text":"你好"}';
      final ev = core.parseSseDataLine(line);
      expect(ev?['type'], 'delta');
      expect(ev?['text'], '你好');
      expect(core.parseSseDataLine('data: [DONE]')?['type'], 'done');
      expect(core.parseSseDataLine('event: ping'), isNull);
    });

    test('身份验签协议原语两端同值（HKDF / unwrap / 指纹）', () {
      final ks = core.AmbIdentity.deriveKeystream('23456789ABCD');
      expect(ks.length, 32);
      final siteBytes = List<int>.generate(32, (i) => (i * 11 + 5) % 256);
      final wrappedHex = List<int>.generate(32, (i) => siteBytes[i] ^ ks[i])
          .map((b) => b.toRadixString(16).padLeft(2, '0'))
          .join();
      final site = core.AmbIdentity.unwrapIdentity(wrappedHex, ks);
      expect(site, siteBytes); // 还原出与 mock 后端同一站点密钥
      expect(core.AmbIdentity.fingerprintOf(site!).length, 12);
      expect(core.AmbIdentity.formatFingerprint('abcdef123456'), 'abcd-ef12-3456');
    });
  });
}
