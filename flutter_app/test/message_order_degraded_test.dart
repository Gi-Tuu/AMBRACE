// 气泡顺序修复 + 思考过载降级提示 测试（2026-09-13 体验修复批，证据 B/C）。
//
// 证据 C：用户气泡用服务器时间、AI 流式占位几乎同时创建 → 同 createdAt 时旧规则按
// 「本地优先」把 AI 占位排到用户上方。新规则：同时刻先按角色（user 在前）再按 id。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/models/message.dart';
import 'package:ai_companion/features/chat/message_appender.dart';
import 'package:ai_companion/widgets/message_bubble.dart';

ChatMessage _msg(int id, String sender, String content, String createdAt,
        {bool isLocal = false, Map<String, dynamic> meta = const {}}) =>
    ChatMessage(
      id: id,
      sessionId: 1,
      senderType: sender,
      content: content,
      createdAt: createdAt,
      isLocal: isLocal,
      extraMeta: meta,
    );

Widget _host(Widget child) => MaterialApp(
      locale: const Locale('zh'),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: child),
    );

void main() {
  // 注意：fromJson 对无时区时间补 Z（按 UTC 解析），本地构造器直传字符串（DateTime.tryParse
  // 同为无时区本地语义）。同测试内统一用同一口径的直构字符串，排序可比。
  const t = '2026-09-12T15:32:42.000';

  testWidgets('① 同 createdAt 的 user + 本地 AI 占位 → user 在前（证据 C 主场景）', (tester) async {
    final list = [
      _msg(-1, 'ai', '', t, isLocal: true), // 流式占位
      _msg(100, 'user', '在忙吗', t),
    ];
    final sorted = MessageAppender.sorted(list);
    expect(sorted.first.senderType, 'user');
    expect(sorted.first.content, '在忙吗');
    expect(sorted.last.isLocal, isTrue);
  });

  testWidgets('② 用户消息被服务端回执替换后顺序仍正确', (tester) async {
    // 替换后的正式消息经 fromJson（补 Z 按 UTC 解析），本地占位也用带 Z 口径，保证可比
    final tz = '$t Z'.replaceAll(' ', '');
    final list = <ChatMessage>[
      _msg(-2, 'user', '在忙吗', tz, isLocal: true),
      _msg(-1, 'ai', '', tz, isLocal: true),
    ];
    MessageAppender.replaceTempUserMessage(list, {
      'id': 100,
      'session_id': 1,
      'sender_type': 'user',
      'content': '在忙吗',
      'created_at': t,
    });
    final sorted = MessageAppender.sorted(list);
    expect(sorted.first.id, 100);
    expect(sorted.first.senderType, 'user');
    expect(sorted.last.senderType, 'ai');
  });

  test('③ 流式结束落正式块（n>1）后顺序稳定：user → 占位 → 块1 → 块2', () {
    final list = <ChatMessage>[
      _msg(-1, 'ai', '…', t, isLocal: true),
      _msg(101, 'ai', '第二块', '2026-09-12T15:32:42.500'),
      _msg(100, 'ai', '第一块', t),
      _msg(100, 'user', '在忙吗', t),
    ];
    final sorted = MessageAppender.sorted(list);
    expect(sorted.map((m) => m.content).toList(), ['在忙吗', '…', '第一块', '第二块']);
  });

  test('④ appendMessageResult：createdAt 用 DateTime.parse（字符串比较会乱序的场景）', () {
    final list = <ChatMessage>[
      _msg(1, 'user', '早', '2026-09-12T09:00:00'),
    ];
    MessageAppender.appendMessageResult(list, {
      'ai_message': {
        'id': 2,
        'session_id': 1,
        'sender_type': 'ai',
        'content': '早呀',
        // 服务器 UTC 时间：fromJson 补 Z → UTC 08:00 = 本地 16:00（晚于 user 09:00）；
        // 旧字符串比较「空格 < T」会把它排到最前（乱序现场）
        'created_at': '2026-09-12 08:00:00',
      },
      'chunks': <Map<String, dynamic>>[
        {
          'id': 3,
          'session_id': 1,
          'sender_type': 'ai',
          'content': '补',
          'created_at': '2026-09-12 08:00:05',
        },
      ],
    }, 'ai_message');
    expect(list.map((m) => m.content).toList(), ['早', '早呀', '补']);
  });

  testWidgets('⑤ 降级标记：AI 气泡下方灰字提示（证据 B）', (tester) async {
    await tester.pumpWidget(_host(MessageBubble(
      message: '……',
      isUser: false,
      degradedReply: true,
    )));
    expect(find.text('（TA 想得太久，一时没说出来）'), findsOneWidget);
  });

  testWidgets('⑥ 无降级标记不显示灰字', (tester) async {
    await tester.pumpWidget(_host(MessageBubble(message: '今天挺好的', isUser: false)));
    expect(find.text('（TA 想得太久，一时没说出来）'), findsNothing);
  });
}
