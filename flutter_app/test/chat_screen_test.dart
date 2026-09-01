import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/chat/chat_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/chat_time_separator.dart';
import 'package:ai_companion/widgets/message_bubble.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 2 B3 聊天页测试：
/// 玻璃顶栏/心情 emoji、TypingIndicator 三点（reduceMotion 静态）、
/// 发送链路（本地消息出现）、用户气泡渐变、时间胶囊、回底按钮、表情面板、
/// 以及 build 期隐患治理（无 setState during build 由全部用例通过隐式保证）。
void main() {
  late FakeApiAdapter api;
  late ChatProvider chat;
  late SettingsProvider settings;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    // 必须配置 baseUrl：ChatProvider.startSession 会以 ws:// 连接，
    // 空 baseUrl 会产生非法 URI 同步抛异常（生产环境总有值）
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');

    // 会话与消息链路
    api.json('GET', '/api/v1/system/health', {
      'status': 'ok',
      'timestamp': '2026-08-28T00:00:00Z',
    });
    api.json('POST', '/api/v1/chat/sessions', {'id': 7, 'character_id': 1});
    api.json('GET', '/api/v1/chat/sessions/7/messages', {
      'messages': [
        {
          'id': 1,
          'session_id': 7,
          'sender_type': 'user',
          'content': '早上好',
          'created_at': '2026-08-28T02:00:00Z',
        },
        {
          'id': 2,
          'session_id': 7,
          'sender_type': 'ai',
          'content': '早呀，今天天气不错',
          'created_at': '2026-08-28T02:20:00Z',
        },
      ],
    });
    // 心情 emoji（mood=80 → 😄）+ 角色聊天偏好
    api.json('GET', '/api/v1/characters/1/states', {
      'character_id': 1,
      'mood': 80,
      'anger': 5,
      'fatigue': 10,
    });
    api.json('GET', '/api/v1/scheduler/settings/1', {'mood_badge_enabled': true});
    // 表情面板（ChatScreen._load + 表情 Sheet）
    api.json('GET', '/api/v1/emojis/packs', []);
    api.json('GET', '/api/v1/emojis/custom', []);

    chat = ChatProvider();
    chat.setCharacter(AICharacter(id: 1, name: 'Alpha'));
    settings = SettingsProvider();
  });

  tearDown(() {
    chat.dispose();
  });

  Widget app() => MultiProvider(
        // 与 main.dart 一致：Provider 包在 MaterialApp 外，push 出的新路由可见
        providers: [
          ChangeNotifierProvider.value(value: chat),
          ChangeNotifierProvider.value(value: settings),
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
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(ChatScreen)))!;

  group('AppBar', () {
    testWidgets('title + mood emoji render', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      expect(find.text('Alpha'), findsOneWidget);
      // mood=80 → 😄（_moodEmojiFor）
      expect(find.text('😄'), findsOneWidget);
      // 未在输入中 → 无三点指示器
      expect(find.byKey(const Key('typingDots')), findsNothing);
    });
  });

  group('TypingIndicator', () {
    Widget host({bool reduceMotion = false, bool disableAnimations = false}) =>
        MultiProvider(
          providers: [ChangeNotifierProvider.value(value: settings)],
          child: MaterialApp(
            home: MediaQuery(
              data: MediaQueryData(disableAnimations: disableAnimations),
              child: Scaffold(
                body: Center(
                  child: TypingIndicator(),
                ),
              ),
            ),
          ),
        );

    testWidgets('default: animated dots present', (tester) async {
      await tester.pumpWidget(host());
      await tester.pump();
      expect(find.byKey(const Key('typingDots')), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('reduceMotion=true: static dots, no animation widget',
        (tester) async {
      await settings.setReduceMotion(true);
      await tester.pumpWidget(host(reduceMotion: true));
      await tester.pump();
      expect(find.byKey(const Key('typingDotsStatic')), findsOneWidget);
      expect(find.byKey(const Key('typingDots')), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('Send message', () {
    testWidgets('send button disabled when empty, enabled with text; send shows bubble',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      final sendBtn = find.ancestor(
        of: find.byIcon(Icons.send),
        matching: find.byType(IconButton),
      );
      // 空输入 → 禁用
      expect(
        tester.widget<IconButton>(sendBtn.first).onPressed,
        isNull,
        reason: 'empty input should disable send',
      );

      await tester.enterText(find.byType(TextField), '你好呀');
      await tester.pump();
      expect(
        tester.widget<IconButton>(sendBtn.first).onPressed,
        isNotNull,
        reason: 'typing text should enable send',
      );

      await tester.tap(sendBtn.first);
      await tester.pumpAndSettle();
      expect(find.text('你好呀'), findsOneWidget);
    });
  });

  group('Bubbles & time separator', () {
    testWidgets('user bubble gradient + ai bubble renders + separator pills',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      // 两条历史消息都渲染（MessageBubble 挂载）
      expect(find.byType(MessageBubble), findsAtLeastNWidgets(2));
      expect(find.text('早上好'), findsOneWidget);
      expect(find.text('早呀，今天天气不错'), findsOneWidget);

      // 用户气泡：带主题色渐变容器
      final gradientBubble = find.byWidgetPredicate((w) =>
          w is Container &&
          w.decoration is BoxDecoration &&
          (w.decoration as BoxDecoration).gradient != null);
      expect(gradientBubble, findsWidgets);

      // #2：时间改回各气泡内部下方，列表不再使用居中时间分隔胶囊
      expect(find.byType(ChatTimeSeparator), findsNothing);
      expect(find.byType(MessageBubble), findsAtLeastNWidgets(2));
    });
  });

  group('Back-to-bottom button', () {
    testWidgets('hidden at bottom, appears after scroll up, returns on tap',
        (tester) async {
      // 构造长列表
      final messages = <Map<String, Object?>>[
        for (var i = 0; i < 60; i++)
          {
            'id': i + 1,
            'session_id': 7,
            'sender_type': i.isEven ? 'user' : 'ai',
            'content': 'msg number $i with some length to occupy space',
            'created_at':
                '2026-08-28T${(i ~/ 60 + 2).toString().padLeft(2, '0')}:${(i % 60).toString().padLeft(2, '0')}:00Z',
          },
      ];
      api.json('GET', '/api/v1/chat/sessions/7/messages', {'messages': messages});

      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      // 初始自动滚到底 → 按钮隐藏
      expect(find.byKey(const Key('backToBottomButton')), findsNothing);

      // 向上滑动 → 按钮出现
      await tester.fling(find.byType(ListView), const Offset(0, 600), 10000);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('backToBottomButton')), findsOneWidget);

      // 点击回底 → 按钮消失
      await tester.tap(find.byKey(const Key('backToBottomButton')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('backToBottomButton')), findsNothing);
    });
  });

  group('Emoji panel', () {
    testWidgets('opens via switch menu and shows pack tabs', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      await tester.tap(find.byTooltip(l10n.switchMode));
      await tester.pumpAndSettle();
      await tester.tap(find.text(l10n.chatEmoji).last);
      await tester.pumpAndSettle();

      // 表情面板 Sheet 标题
      expect(find.text(l10n.emojiPack), findsOneWidget);
    });
  });

  group('Entrance (reduceMotion)', () {
    testWidgets('reduceMotion=true: new message appears without animation',
        (tester) async {
      await settings.setReduceMotion(true);
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField), '秒出现');
      await tester.pump();
      await tester.tap(find.byIcon(Icons.send));
      // 不 settle，只 pump 极短时间：reduceMotion 下也应已完整可见
      await tester.pump(const Duration(milliseconds: 20));
      expect(find.text('秒出现'), findsOneWidget);
    });
  });

  group('More panel', () {
    testWidgets('voice call entry renders; tap without session shows hint', (tester) async {
      // 强制会话创建失败（startSession 报错 → sessionId 保持 null），走到「无会话」守卫。
      api.handle('POST', '/api/v1/chat/sessions',
          (_) => FakeApiAdapter.body({'detail': 'fail'}, 500));

      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      await tester.tap(find.byTooltip(l10n.moreFunctions));
      await tester.pumpAndSettle();
      // 语音通话入口出现在「更多功能」面板（微信式 + 按钮区）
      expect(find.text(l10n.voiceCallEntry), findsOneWidget);
      expect(find.text(l10n.voiceCallEntrySub), findsOneWidget);

      // 无会话点击 → 提示先选择角色（不进入通话页）
      await tester.tap(find.text(l10n.voiceCallEntry));
      await tester.pumpAndSettle();
      expect(find.text(l10n.chooseFriendFirst), findsOneWidget);
    });
  });
}
