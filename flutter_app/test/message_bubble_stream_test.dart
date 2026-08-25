
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ai_companion/widgets/message_bubble.dart';

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
        home: Scaffold(body: child),
      );

  testWidgets('流式渲染：isStreaming=true 时正文末尾出现打字机光标', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '正在输入',
      isUser: false,
      isStreaming: true,
    )));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('正在输入'), findsOneWidget);
    expect(find.text('▍'), findsOneWidget);
  });

  testWidgets('非流式：isStreaming=false 不显示光标', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '已发送',
      isUser: false,
      isStreaming: false,
    )));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('已发送'), findsOneWidget);
    expect(find.text('▍'), findsNothing);
  });

  testWidgets('用户气泡不显示光标（仅 AI 流式）', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '用户消息',
      isUser: true,
      isStreaming: true,
    )));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('▍'), findsNothing);
  });
}
