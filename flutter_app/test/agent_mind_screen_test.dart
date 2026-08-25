import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/screens/character/agent_mind_screen.dart';

void main() {
  testWidgets('AgentMindScreen builds with 5 tabs', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: const AgentMindScreen(characterId: 1, characterName: 'test'),
      ),
    );
    // 测试环境无后端：_load 的网络请求会失败进入错误态，TabBar 始终渲染。
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.byType(AgentMindScreen), findsOneWidget);
    expect(find.byType(DefaultTabController), findsOneWidget);
    expect(find.byType(TabBar), findsOneWidget);
    expect(find.byType(Tab), findsNWidgets(5));
    expect(find.text('AI 内心世界'), findsOneWidget);
    expect(find.text('记忆召回'), findsOneWidget);
    expect(find.text('运行笔记'), findsOneWidget);
    expect(find.text('最近复盘'), findsOneWidget);
    expect(find.text('任务记录'), findsOneWidget);
    expect(find.text('工具轨迹'), findsOneWidget);
  });
}
