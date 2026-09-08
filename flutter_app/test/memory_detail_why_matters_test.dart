import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/memory/memory_detail_screen.dart';
import 'package:ai_companion/models/memory.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:provider/provider.dart';

import 'fake_api_adapter.dart';

/// R7（工具轨迹治理批次三）：记忆详情页「这件事为什么重要」完整意义卡。
/// 卡片侧 2 行省略保留；详情页补完整 SelectableText（不截断、可长按复制）；
/// 无意义文案时该卡不渲染。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    // 记忆链条子节点：空
    api.json('DELETE', '/api/v1/memories/1/tree', {'children': []});
  });

  Memory mem({String? whyItMatters}) => Memory(
        id: 1,
        memoryType: 'event',
        source: 'chat',
        title: '第一次见面',
        content: '在咖啡馆聊了整个下午。',
        importance: 3,
        createdAt: '2026-09-01T10:00:00',
        whyItMatters: whyItMatters,
      );

  Widget app(Memory memory, {Locale locale = const Locale('zh')}) =>
      ChangeNotifierProvider(
        create: (_) => SettingsProvider(),
        child: MaterialApp(
          locale: locale,
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: MemoryDetailScreen(memory: memory),
        ),
      );

  testWidgets('长文案：意义卡完整渲染，无 maxLines/ellipsis，可长按复制', (tester) async {
    final longWhy = '${'这是用户第一次主动分享童年经历，'.padRight(120, '细')}'
        '标志着信任关系的建立，后续回忆与共鸣都应以此为核心展开，'
        '也是触发长程陪伴话题的关键锚点。';
    await tester.pumpWidget(app(mem(whyItMatters: longWhy)));
    await tester.pumpAndSettle();

    // 标题出现（zh）
    expect(find.text('这件事为什么重要'), findsOneWidget);

    // SelectableText 全文渲染（非 Text，且无 maxLines/ellipsis 截断）
    final selectable = find
        .byWidgetPredicate((w) => w is SelectableText && w.data == longWhy);
    expect(selectable, findsOneWidget);
    final st = tester.widget<SelectableText>(selectable);
    expect(st.maxLines, isNull, reason: '意义卡不设 maxLines');
    expect(find.textContaining(longWhy.substring(0, 30)), findsOneWidget);
  });

  testWidgets('whyItMatters 为空/null：意义卡不渲染', (tester) async {
    await tester.pumpWidget(app(mem(whyItMatters: null)));
    await tester.pumpAndSettle();
    expect(find.text('这件事为什么重要'), findsNothing);
    expect(find.byType(SelectableText), findsNothing);

    // 空白字符串同样不渲染
    await tester.pumpWidget(app(mem(whyItMatters: '   ')));
    await tester.pumpAndSettle();
    expect(find.text('这件事为什么重要'), findsNothing);
  });

  testWidgets('英文本地化标题正确', (tester) async {
    final longWhy = 'Trust anchor for all future empathic responses.' * 3;
    await tester.pumpWidget(app(mem(whyItMatters: longWhy), locale: const Locale('en')));
    await tester.pumpAndSettle();
    expect(find.text('Why it matters'), findsOneWidget);
    expect(find.text(longWhy), findsOneWidget);
  });
}
