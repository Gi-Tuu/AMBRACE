import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ai_companion/widgets/message_bubble.dart';

/// 思考块「内心活动」变体（2026-09-10）：auto_awesome 图标 +「TA 的内心」+ 斜体；
/// 工具结果折叠块维持原冷样式（非斜体）。
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

  const monologue = '轩刚好回来，我先把肉盛出来。';

  testWidgets('思考非空且开关打开：渲染内心活动变体（图标/标签/斜体）', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '肉盛好了。',
      isUser: false,
      reasoning: monologue,
      showReasoning: true,
    )));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('TA 的内心'), findsOneWidget);
    expect(find.byIcon(Icons.auto_awesome), findsOneWidget);
    // 折叠态预览为斜体
    final preview = tester.widget<Text>(find.text(monologue));
    expect(preview.style?.fontStyle, FontStyle.italic);

    // 展开后正文仍为斜体
    await tester.tap(find.text('TA 的内心'));
    await tester.pump(const Duration(milliseconds: 300));
    final body = tester.widget<Text>(find.text(monologue));
    expect(body.style?.fontStyle, FontStyle.italic);
  });

  testWidgets('思考为空 / 开关关闭：不渲染思考块', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '肉盛好了。',
      isUser: false,
      reasoning: '',
      showReasoning: true,
    )));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('TA 的内心'), findsNothing);
    expect(find.byIcon(Icons.auto_awesome), findsNothing);

    await tester.pumpWidget(wrap(const MessageBubble(
      message: '肉盛好了。',
      isUser: false,
      reasoning: monologue,
      showReasoning: false,
    )));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('TA 的内心'), findsNothing);
  });

  testWidgets('工具结果折叠块样式不变（非斜体）', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MessageBubble(
      message: '查到了。',
      isUser: false,
      toolResults: [
        {'tool': 'search', 'ok': true, 'summary': '命中 3 条结果'},
      ],
    )));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    final detail = tester.widget<Text>(find.textContaining('命中 3 条结果'));
    expect(detail.style?.fontStyle, isNull);
  });
}
