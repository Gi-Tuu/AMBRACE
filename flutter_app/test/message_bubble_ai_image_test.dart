
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/models/message.dart';
import 'package:ai_companion/widgets/message_bubble.dart';

/// AI 生图消息标注（P0，2026-09-10）：类型角标「AI 生图」+ IMG_TEXT 配文小字。
/// 重点回归：角标**不受 showTools 开关控制**（能力 chip 才受控）。
void main() {
  Widget wrap(Widget child) => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(body: SingleChildScrollView(child: child)),
      );

  const img = 'https://example.com/gen_1.png';

  testWidgets('生图消息：showTools=false 时角标仍显示，配文小字在角标下方',
      (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '……就这一张。',
      isUser: false,
      imageUrl: img,
      isAiGeneratedImage: true,
      showTools: false,
    )));
    await tester.pumpAndSettle();

    final badge = find.text('AI 生图');
    final caption = find.text('……就这一张。');
    expect(badge, findsOneWidget, reason: '角标必须始终显示，不受 showTools 控制');
    expect(caption, findsOneWidget, reason: '配文只应渲染一次（图片块内小字）');
    // 配文在角标下方
    expect(
      tester.getTopLeft(caption).dy,
      greaterThan(tester.getTopLeft(badge).dy),
      reason: '配文小字应在角标下方',
    );
    // 能力 chip 仍受 showTools 门控（确认角标没接进 showTools）
    expect(find.text('调用能力'), findsNothing);
  });

  testWidgets('生图消息：showTools=true 且带 tools 时角标与能力 chip 同时存在',
      (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '……就这一张。',
      isUser: false,
      imageUrl: img,
      isAiGeneratedImage: true,
      tools: ['生图'],
      showTools: true,
    )));
    await tester.pumpAndSettle();

    expect(find.text('AI 生图'), findsOneWidget);
    expect(find.text('调用能力'), findsOneWidget, reason: '能力区受 showTools 控制');
  });

  testWidgets('空配文生图消息：只渲染角标，不留配文行',
      (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '',
      isUser: false,
      imageUrl: img,
      isAiGeneratedImage: true,
      showTools: false,
    )));
    await tester.pumpAndSettle();

    expect(find.text('AI 生图'), findsOneWidget);
    // 不渲染空文本的 Text（避免残留空隙）
    expect(
      find.byWidgetPredicate(
        (w) => w is Text && (w.data ?? '').trim().isEmpty,
      ),
      findsNothing,
    );
  });

  testWidgets('普通文本消息：状态更新 marker 小字行为不变',
      (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '我今天很开心【状态更新：心情=愉快】',
      isUser: false,
      showTools: false,
    )));
    await tester.pumpAndSettle();

    expect(find.text('我今天很开心'), findsOneWidget);
    expect(find.text('状态更新：心情=愉快'), findsOneWidget);
    expect(find.text('AI 生图'), findsNothing, reason: '非图片消息不显示生图角标');
  });

  test('ChatMessage.isAiGeneratedImage 判定（新 meta / 老 meta / 非生图 / 用户消息）', () {
    ChatMessage mk({
      required String sender,
      String? imageUrl,
      Map<String, dynamic> meta = const {},
    }) =>
        ChatMessage(
          id: 1,
          sessionId: 1,
          senderType: sender,
          content: '',
          createdAt: '2026-09-10T10:00:00Z',
          imageUrl: imageUrl,
          extraMeta: meta,
        );

    // 新数据：kind=ai_image
    expect(
      mk(sender: 'ai', imageUrl: '/u/a.png', meta: {'kind': 'ai_image'}).isAiGeneratedImage,
      isTrue,
    );
    // 老数据：仅 gen_image=true
    expect(
      mk(sender: 'ai', imageUrl: '/u/a.png', meta: {'gen_image': true}).isAiGeneratedImage,
      isTrue,
    );
    // 老数据：仅 tools 含「生图」
    expect(
      mk(sender: 'ai', imageUrl: '/u/a.png', meta: {
        'tools': ['生图']
      }).isAiGeneratedImage,
      isTrue,
    );
    // 无图 / 用户消息 / 普通图片（无 meta）均不标
    expect(mk(sender: 'ai', meta: {'kind': 'ai_image'}).isAiGeneratedImage, isFalse);
    expect(
      mk(sender: 'user', imageUrl: '/u/a.png', meta: {'kind': 'ai_image'}).isAiGeneratedImage,
      isFalse,
    );
    expect(mk(sender: 'ai', imageUrl: '/u/a.png').isAiGeneratedImage, isFalse);
  });
}
