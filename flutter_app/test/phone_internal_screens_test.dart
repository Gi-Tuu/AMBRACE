import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/phone/ai_interaction_screen.dart';
import 'package:ai_companion/screens/phone/phone_app_screens.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P3 手机内页测试：
/// 微信畅聊首页/聊天页/记录箱（气泡渐变、AuroraCard 行、EmptyState 三态）
/// + 内置 App 七页（相册/市场/日历/浏览器/主题/备忘录/设置）。
void main() {
  late FakeApiAdapter api;
  late SettingsProvider settings;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    settings = SettingsProvider();
    _mockNavigation(api);
    _mockInnerLists(api);
  });

  Widget app() => MultiProvider(
        providers: [ChangeNotifierProvider.value(value: settings)],
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const AiInteractionScreen(),
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(AiInteractionScreen)))!;

  /// 外页 → 桌面 → 畅聊首页（l10n 须在 push 前取）
  Future<AppLocalizations> openWechatHome(WidgetTester tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);
    await tester.tap(find.text('Alpha'));
    await tester.pumpAndSettle();
    await tester.tap(find.text(l10n.appChat));
    await tester.pumpAndSettle();
    return l10n;
  }
  group('Wechat home', () {
    testWidgets('AppBar + DM entry renders name/last message', (tester) async {
      final l10n = await openWechatHome(tester);

      expect(find.text(l10n.chatOf('Alpha')), findsOneWidget);
      expect(find.byTooltip(l10n.createGroup), findsOneWidget);
      expect(find.text('Beta'), findsOneWidget);
      expect(find.textContaining('hi from beta'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('empty chats -> EmptyState with noChatRecords', (tester) async {
      // 须在 pump 前覆写：外页装载的 chats 会传给畅聊首页
      api.json('GET', '/api/v1/ai-chats', {'items': []});
      api.json('GET', '/api/v1/chat-groups', {'items': []});
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);
      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();
      await tester.tap(find.text(l10n.appChat));
      await tester.pumpAndSettle();

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noChatRecords), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('tap DM entry opens chat page: gradient bubble + readOnly bar',
        (tester) async {
      final l10n = await openWechatHome(tester);

      await tester.tap(find.text('Beta'));
      await tester.pumpAndSettle();

      // mine 渐变气泡 + 非 mine 气泡
      final gradientBubble = find.byWidgetPredicate((w) =>
          w is Container &&
          w.decoration is BoxDecoration &&
          (w.decoration as BoxDecoration).gradient != null);
      expect(gradientBubble, findsOneWidget);
      expect(find.text('hello from alpha'), findsOneWidget);
      expect(find.text('hi from beta'), findsOneWidget);
      // 只读输入栏
      expect(find.text(l10n.readOnly), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('archive: year/month/day sections render, tap year collapses',
        (tester) async {
      final l10n = await openWechatHome(tester);
      await tester.tap(find.text('Beta'));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip(l10n.archiveBox));
      await tester.pumpAndSettle();

      expect(find.text('2026'), findsOneWidget);
      expect(find.text(l10n.month8), findsOneWidget);
      expect(find.text(l10n.dayLabel(8, 28)), findsOneWidget);

      // 点击年份 → 收起（月份行消失）
      await tester.tap(find.text('2026'));
      await tester.pumpAndSettle();
      expect(find.text(l10n.month8), findsNothing);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('archive error -> loadFailed + retry recovers', (tester) async {
      final l10n = await openWechatHome(tester);
      await tester.tap(find.text('Beta'));
      await tester.pumpAndSettle();

      api.json('GET', '/api/v1/ai-chats', {'detail': 'boom'}, status: 500);
      await tester.tap(find.byTooltip(l10n.archiveBox));
      await tester.pumpAndSettle();

      expect(find.text(l10n.loadFailed), findsOneWidget);
      expect(find.text(l10n.retry), findsOneWidget);

      api.json('GET', '/api/v1/ai-chats', {'items': _chats()});
      await tester.tap(find.text(l10n.retry));
      await tester.pumpAndSettle();
      expect(find.text('2026'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('archive empty -> noArchive EmptyState', (tester) async {
      final l10n = await openWechatHome(tester);
      await tester.tap(find.text('Beta'));
      await tester.pumpAndSettle();

      api.json('GET', '/api/v1/ai-chats', {'items': []});
      await tester.tap(find.byTooltip(l10n.archiveBox));
      await tester.pumpAndSettle();

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noArchive), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('Built-in apps', () {
    testWidgets('MarketScreen: rows + installed/download + onRestore callback',
        (tester) async {
      final restored = <String>[];
      await tester.pumpWidget(MultiProvider(
        providers: [ChangeNotifierProvider.value(value: settings)],
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: MarketScreen(
            catalog: const [
              {'key': 'chat', 'label': 'Chat'},
              {'key': 'theme', 'label': 'Theme'},
            ],
            installedKeys: const {'chat'},
            onRestore: (key) async => restored.add(key),
          ),
        ),
      ));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(
          tester.element(find.byType(MarketScreen)))!;

      expect(find.text('Chat'), findsOneWidget);
      expect(find.text(l10n.installed), findsOneWidget);
      expect(find.text('Theme'), findsOneWidget);
      expect(find.text(l10n.download), findsOneWidget);

      await tester.tap(find.text(l10n.download));
      await tester.pumpAndSettle();
      expect(restored, contains('theme'));
      expect(find.text(l10n.restoredToDesktop), findsOneWidget);
    });

    testWidgets('MemoScreen: empty noMemos; with data row + delete button',
        (tester) async {
      await tester.pumpWidget(_hostApp(const MemoScreen(characterId: 1)));
      await tester.pumpAndSettle();
      var l10n = AppLocalizations.of(tester.element(find.byType(MemoScreen)))!;
      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noMemos), findsOneWidget);

      api.json('GET', '/api/v1/phone-desktop/memos', {
        'items': [
          {'id': 1, 'text': 'buy milk', 'author': 'Alpha', 'created_at': '2026-08-28T02:00:00Z'},
        ],
      });
      // UniqueKey 强制重建 State（同型 widget 二次 pump 不走 initState）
      await tester.pumpWidget(_hostApp(MemoScreen(key: UniqueKey(), characterId: 1)));
      await tester.pumpAndSettle();
      l10n = AppLocalizations.of(tester.element(find.byType(MemoScreen)))!;
      expect(find.textContaining('buy milk'), findsOneWidget);
      expect(find.byIcon(Icons.delete_outline), findsOneWidget);
    });

    testWidgets('BrowserScreen: empty history -> searchHint EmptyState; with data row',
        (tester) async {
      await tester.pumpWidget(_hostApp(const BrowserScreen(characterId: 1)));
      await tester.pumpAndSettle();
      var l10n = AppLocalizations.of(tester.element(find.byType(BrowserScreen)))!;
      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.searchHint), findsOneWidget);

      api.json('GET', '/api/v1/phone-desktop/browser-history', {
        'items': [
          {'id': 1, 'query': 'aurora ui design', 'created_at': '2026-08-28T02:00:00Z'},
        ],
      });
      await tester.pumpWidget(_hostApp(BrowserScreen(key: UniqueKey(), characterId: 1)));
      await tester.pumpAndSettle();
      l10n = AppLocalizations.of(tester.element(find.byType(BrowserScreen)))!;
      expect(find.text('aurora ui design'), findsOneWidget);
      expect(find.byIcon(Icons.close), findsOneWidget);
    });

    testWidgets('AlbumScreen: both tabs empty states', (tester) async {
      await tester.pumpWidget(_hostApp(const AlbumScreen()));
      await tester.pumpAndSettle();
      var l10n = AppLocalizations.of(tester.element(find.byType(AlbumScreen)))!;
      expect(find.text(l10n.noAiImages), findsOneWidget);

      await tester.tap(find.text(l10n.myUploads));
      await tester.pumpAndSettle();
      expect(find.text(l10n.noUploadsHint), findsOneWidget);
    });

    testWidgets('CalendarScreen: weekday header + today cell render',
        (tester) async {
      await tester.pumpWidget(_hostApp(const CalendarScreen(characterId: 1)));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(tester.element(find.byType(CalendarScreen)))!;

      expect(find.text(l10n.weekday1), findsOneWidget);
      final today = DateTime.now();
      expect(find.text('${today.day}'), findsOneWidget);
      // 底部提示文字渲染
      expect(find.text(l10n.calendarHint), findsOneWidget);
    });

    testWidgets('ThemeScreen grid + upload tile; SettingsScreen placeholder card',
        (tester) async {
      await tester.pumpWidget(_hostApp(ThemeScreen(current: null, onChanged: (_) async {})));
      await tester.pumpAndSettle();
      var l10n = AppLocalizations.of(tester.element(find.byType(ThemeScreen)))!;
      expect(find.text(l10n.themeStarryNight), findsOneWidget);
      expect(find.text(l10n.uploadWallpaper), findsOneWidget);

      await tester.pumpWidget(_hostApp(const SettingsScreen()));
      await tester.pumpAndSettle();
      l10n = AppLocalizations.of(tester.element(find.byType(SettingsScreen)))!;
      expect(find.text(l10n.virtualPhone), findsOneWidget);
      expect(find.byType(AuroraCard), findsOneWidget);
    });
  });
}

Widget _hostApp(Widget child) => MultiProvider(
      providers: [ChangeNotifierProvider(create: (_) => SettingsProvider())],
      child: MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: child,
      ),
    );

List<Map<String, Object?>> _chats() => [
      {
        'id': 1,
        'character_a_id': 1,
        'character_a_name': 'Alpha',
        'character_b_id': 2,
        'character_b_name': 'Beta',
        'speaker_id': 1,
        'speaker_name': 'Alpha',
        'round_seq': 1,
        'content': 'hello from alpha',
        'created_at': '2026-08-28T02:00:00Z',
      },
      {
        'id': 2,
        'character_a_id': 1,
        'character_a_name': 'Alpha',
        'character_b_id': 2,
        'character_b_name': 'Beta',
        'speaker_id': 2,
        'speaker_name': 'Beta',
        'round_seq': 2,
        'content': 'hi from beta',
        'created_at': '2026-08-28T02:05:00Z',
      },
    ];

void _mockNavigation(FakeApiAdapter api) {
  api.json('GET', '/api/v1/characters', {
    'characters': [
      {'id': 1, 'name': 'Alpha', 'personality': 'kind'},
    ],
  });
  api.json('GET', '/api/v1/ai-chats', {'items': _chats()});
  api.json('GET', '/api/v1/auth/profile', {'ai_social_enabled': true});
  api.json('GET', '/api/v1/life/state', {'phase': 'morning'});
  api.json('GET', '/api/v1/characters/1/states',
      {'character_id': 1, 'mood': 80, 'anger': 5, 'fatigue': 10});
  api.json('GET', '/api/v1/privacy/1/status', {'enabled': false});
  api.json('GET', '/api/v1/phone-desktop/layouts', {
    'apps': [],
    'catalog': [
      {'key': 'chat'},
    ],
    'browser_plugin_enabled': false,
    'wallpaper': null,
  });
  api.json('GET', '/api/v1/chat-groups', {'items': []});
  api.json('GET', '/api/v1/chat-groups/5/messages', {'items': []});
}

void _mockInnerLists(FakeApiAdapter api) {
  api.json('GET', '/api/v1/phone-desktop/photos',
      {'ai_photos': [], 'user_photos': []});
  api.json('GET', '/api/v1/phone-desktop/browser-history', {'items': []});
  api.json('GET', '/api/v1/phone-desktop/memos', {'items': []});
  api.json('GET', '/api/v1/phone-desktop/calendar-notes', {'items': []});
  api.json('GET', '/api/v1/life/schedules', {'items': []});
}
