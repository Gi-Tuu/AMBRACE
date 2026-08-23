
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/chat/chat_screen.dart';

void main() {
  Future<void> pumpChat(WidgetTester tester, ChatProvider chat) async {
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<SettingsProvider>(create: (_) => SettingsProvider()),
          ChangeNotifierProvider<ChatProvider>.value(value: chat),
        ],
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const ChatScreen(),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
  }

  testWidgets('收起「更多功能」面板后输入框不自动聚焦', (WidgetTester tester) async {
    final chat = ChatProvider();
    chat.setUserId(1);

    // 先挂载（此时 currentCharacter 为 null，避免 didChangeDependencies 同步触发
    // startSession 的 notifyListeners 造成 build 阶段断言），再模拟从好友列表进入聊天。
    await pumpChat(tester, chat);
    expect(find.byType(TextField), findsNothing);

    chat.setCharacter(AICharacter(id: 1, name: '测试AI'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    TextField input() => tester.widget<TextField>(find.byType(TextField));
    expect(find.byType(TextField), findsOneWidget);

    // 初始输入框未聚焦（未主动点击输入框）
    expect(input().focusNode!.hasFocus, isFalse);
    expect(find.text('图片'), findsNothing);

    // 打开「更多功能」面板：输入框应保持未聚焦
    await tester.tap(find.byTooltip('更多功能'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('图片'), findsOneWidget);
    expect(input().focusNode!.hasFocus, isFalse);

    // 再点一次收回面板：关键断言——输入框仍未聚焦（不误触发键盘弹起）
    await tester.tap(find.byTooltip('更多功能'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('图片'), findsNothing);
    expect(input().focusNode!.hasFocus, isFalse);
  });
}
