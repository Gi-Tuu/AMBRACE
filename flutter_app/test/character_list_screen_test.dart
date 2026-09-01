import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/character/character_edit_screen.dart';
import 'package:ai_companion/features/chat/chat_screen.dart';
import 'package:ai_companion/screens/home/character_list_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/character_list_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

/// Aurora Phase 2 B2 AI 好友列表测试：
/// GlassBar 顶栏、搜索过滤、Aurora 卡片 + 未读红点、FAB 创建、
/// EmptyState 空态/错误态、reduceMotion 入场直显。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient().dio.httpClientAdapter = _FakeAdapter.instance;
    _FakeAdapter.reset();
  });

  Widget l10nApp({required Widget home}) => MultiProvider(
        // 与 main.dart 一致：Provider 包在 MaterialApp 外，push 出的新路由可见
        providers: [
          ChangeNotifierProvider(create: (_) => ChatProvider()),
          ChangeNotifierProvider(create: (_) => SettingsProvider()),
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
          home: home,
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(CharacterListScreen)))!;

  group('AppBar', () {
    testWidgets('title + menu + 右上角工具箱存在；无 FAB / 标题内无邮件图标',
        (tester) async {
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      final l10n = l10nOf(tester);
      expect(find.text(l10n.aiFriendTitle), findsOneWidget);
      expect(find.byTooltip(l10n.menu), findsOneWidget);
      // #16/#17：创建好友 FAB 已移除，改为右上角工具箱
      expect(find.byType(FloatingActionButton), findsNothing);
      expect(find.byTooltip(l10n.toolboxTitle), findsOneWidget);
      // 邮件/批准入口收进工具箱，标题栏不再常驻
      expect(find.byIcon(Icons.mail_outline), findsNothing);
      expect(find.byIcon(Icons.add), findsNothing);

      // 打开工具箱：三入口齐全
      await tester.tap(find.byTooltip(l10n.toolboxTitle));
      await tester.pumpAndSettle();
      expect(find.text(l10n.weaveLibraryTitle), findsOneWidget);
      expect(find.text(l10n.dyApprovalsTitle), findsOneWidget);
      expect(find.text(l10n.createFriend), findsOneWidget);
    });

    testWidgets('工具箱内点「创建好友」进入 CharacterEditScreen', (tester) async {
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      final l10n = l10nOf(tester);
      await tester.tap(find.byTooltip(l10n.toolboxTitle));
      await tester.pumpAndSettle();
      await tester.tap(find.text(l10n.createFriend).last);
      await tester.pumpAndSettle();
      expect(find.byType(CharacterEditScreen), findsOneWidget);
    });
  });

  group('Search', () {
    testWidgets('filters list by name', (tester) async {
      _FakeAdapter.charactersResponse([
        {'id': 1, 'name': 'Alpha', 'personality': 'p1'},
        {'id': 2, 'name': 'Beta', 'personality': 'p2'},
      ]);
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('Beta'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('searchField')), 'alp');
      await tester.pumpAndSettle();
      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('Beta'), findsNothing);

      // 无匹配 → EmptyState 渲染 noMatchingFriend
      await tester.enterText(find.byKey(const Key('searchField')), 'zzz');
      await tester.pumpAndSettle();
      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10nOf(tester).noMatchingFriend), findsOneWidget);
    });
  });

  group('Empty / error state', () {
    testWidgets('empty list renders EmptyState with noAiFriend',
        (tester) async {
      _FakeAdapter.charactersResponse([]);
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10nOf(tester).noAiFriend), findsOneWidget);
    });

    testWidgets('load failure renders error EmptyState with retry',
        (tester) async {
      _FakeAdapter.failCharacters();
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      final l10n = l10nOf(tester);
      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.loadFailedCheckServer), findsOneWidget);
      expect(find.text(l10n.retry), findsOneWidget);
    });
  });

  group('CharacterListCard', () {
    AICharacter char(String name, {int id = 1}) => AICharacter(
          id: id,
          name: name,
          personality: 'kind and curious',
        );

    Widget cardHost(CharacterListCard card) => MaterialApp(
          home: Scaffold(
            body: Center(child: card),
          ),
        );

    testWidgets('renders name/subtitle and unread badge', (tester) async {
      await tester.pumpWidget(cardHost(CharacterListCard(
        character: char('Alpha'),
        unread: 3,
        onTap: () {},
        onLongPress: () {},
      )));
      await tester.pumpAndSettle();

      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('kind and curious'), findsOneWidget);
      expect(find.byKey(const Key('characterUnreadBadge')), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
    });

    testWidgets('no badge without unread; tap/long-press callbacks fire',
        (tester) async {
      var tapped = false;
      var longPressed = false;
      await tester.pumpWidget(cardHost(CharacterListCard(
        character: char('Alpha'),
        unread: null,
        onTap: () => tapped = true,
        onLongPress: () => longPressed = true,
      )));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('characterUnreadBadge')), findsNothing);

      await tester.tap(find.text('Alpha'));
      await tester.longPress(find.text('Alpha'));
      expect(tapped, isTrue);
      expect(longPressed, isTrue);
    });
  });

  group('Character tap integration', () {
    testWidgets('tap card sets ChatProvider and pushes ChatScreen',
        (tester) async {
      _FakeAdapter.charactersResponse([
        {'id': 1, 'name': 'Alpha', 'personality': 'p1'},
        {'id': 2, 'name': 'Beta', 'personality': 'p2'},
      ]);
      final chat = ChatProvider();
      await tester.pumpWidget(MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: chat),
          ChangeNotifierProvider(create: (_) => SettingsProvider()),
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
          home: const CharacterListScreen(),
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('Beta'), findsOneWidget);

      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle(); // 入场动画结束（此后无帧调度）
      await tester.pump(const Duration(milliseconds: 250)); // 推进 onTap 的 200ms 延迟，触发 push
      await tester.pumpAndSettle(); // 路由转场完成
      expect(chat.currentCharacter?.id, 1);
      expect(find.byType(ChatScreen), findsOneWidget);
    });
  });

  group('Toolbox', () {
    testWidgets('tooltip uses toolboxTitle; no FAB', (tester) async {
      _FakeAdapter.charactersResponse([]);
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pumpAndSettle();

      final l10n = l10nOf(tester);
      // #16：入口收敛到右上角工具箱，tooltip 为工具箱；不再有 FAB
      expect(find.byTooltip(l10n.toolboxTitle), findsOneWidget);
      expect(find.byType(FloatingActionButton), findsNothing);
    });
  });

  group('Entrance animation', () {
    testWidgets('reduceMotion=true: cards visible immediately, no stagger',
        (tester) async {
      _FakeAdapter.charactersResponse([
        {'id': 1, 'name': 'Alpha', 'personality': 'p1'},
        {'id': 2, 'name': 'Beta', 'personality': 'p2'},
      ]);
      final s = SettingsProvider();
      await s.setReduceMotion(true);
      await tester.pumpWidget(MultiProvider(
        providers: [
          ChangeNotifierProvider(create: (_) => ChatProvider()),
          ChangeNotifierProvider.value(value: s),
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
          home: const CharacterListScreen(),
        ),
      ));
      // 只 pump 少量帧（不给错峰计时器时间），reduceMotion 下应直接渲染完成
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 30));
      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('Beta'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('default: cards appear with stagger (2nd item delayed)',
        (tester) async {
      _FakeAdapter.charactersResponse([
        {'id': 1, 'name': 'Alpha', 'personality': 'p1'},
        {'id': 2, 'name': 'Beta', 'personality': 'p2'},
      ]);
      await tester.pumpWidget(l10nApp(home: const CharacterListScreen()));
      await tester.pump(); // 首帧
      await tester.pump(const Duration(milliseconds: 10));

      // 第一项（0ms 延迟）已入场，第二项（50ms 延迟）仍在等待
      final betaOpacity = tester.widget<AnimatedOpacity>(
        find
            .ancestor(
              of: find.text('Beta'),
              matching: find.byType(AnimatedOpacity),
            )
            .first,
      );
      expect(betaOpacity.opacity, 0);
      expect(find.text('Alpha'), findsOneWidget);

      await tester.pumpAndSettle();
      expect(find.text('Beta'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  });
}

/// Dio HttpClientAdapter 桩：按 path 返回固定 JSON（不产生真实网络请求）。
class _FakeAdapter implements HttpClientAdapter {
  static final _FakeAdapter instance = _FakeAdapter();

  ResponseBody Function(RequestOptions options)? _handler;

  static void reset() {
    instance._handler = null;
  }

  static void charactersResponse(List<Map<String, Object?>> chars) {
    instance._handler = (options) {
      if (options.uri.path == '/api/v1/characters') {
        return _jsonBody({'characters': chars}, 200);
      }
      return _jsonBody({'detail': 'not found'}, 404);
    };
  }

  static void failCharacters() {
    instance._handler = (options) {
      if (options.uri.path == '/api/v1/characters') {
        return _jsonBody({'detail': 'boom'}, 500);
      }
      return _jsonBody({'detail': 'not found'}, 404);
    };
  }

  static ResponseBody _jsonBody(Map<String, Object?> data, int status) =>
      ResponseBody.fromString(
        jsonEncode(data),
        status,
        headers: {
          Headers.contentTypeHeader: [Headers.jsonContentType],
        },
      );

  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final handler = _handler;
    if (handler != null) return handler(options);
    return _jsonBody({'detail': 'not found'}, 404);
  }
}
