import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/phone/ai_interaction_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';
import 'package:ai_companion/widgets/privacy_lock_view.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P2 手机页/AI互动测试：
/// 外页玻璃顶栏/私聊开关卡/角色手机方块 AuroraCard/空态错误态、
/// 桌面时钟玻璃卡 + 编辑模式、App 图标按压、隐私锁脉冲（reduceMotion 受控）。
void main() {
  late FakeApiAdapter api;
  late SettingsProvider settings;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    settings = SettingsProvider();
    _mockDefault(api);
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

  group('Outer page', () {
    testWidgets('AppBar: title/menu/myPhone/switch exist', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.text(l10n.phoneShort), findsOneWidget);
      expect(find.byTooltip(l10n.menu), findsOneWidget);
      expect(find.byTooltip(l10n.myPhone), findsOneWidget);
      expect(find.text(l10n.aiPrivateChat), findsOneWidget);
    });

    testWidgets('phone tile renders name/last message/present line in AuroraCard',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('last message text'), findsOneWidget);
      // 此刻行：phase=morning + mood=80
      expect(find.text(l10n.presentLine(l10n.phaseMorning, l10n.moodGreat)),
          findsOneWidget);
      expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
    });

    testWidgets('press scale structure: AnimatedScale present by default, '
        'absent under reduceMotion', (tester) async {
      // 默认：AuroraCard _Pressable 内含 AnimatedScale（按压 0.98）
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      expect(
        find.descendant(
          of: find.byType(AuroraCard),
          matching: find.byType(AnimatedScale),
        ),
        findsOneWidget,
      );

      // reduceMotion：_Pressable 退化为普通 GestureDetector
      await settings.setReduceMotion(true);
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      expect(
        find.descendant(
          of: find.byType(AuroraCard),
          matching: find.byType(AnimatedScale),
        ),
        findsNothing,
      );
    });

    testWidgets('empty characters -> EmptyState with noCharacters',
        (tester) async {
      api.json('GET', '/api/v1/characters', {'characters': []});
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noCharacters), findsOneWidget);
      expect(find.text(l10n.createRoleHint), findsOneWidget);
    });

    testWidgets('load failure -> error EmptyState with retry that recovers',
        (tester) async {
      api.json('GET', '/api/v1/characters', {'detail': 'boom'}, status: 500);
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.loadFailed), findsOneWidget);
      expect(find.text(l10n.retry), findsOneWidget);

      // 恢复 mock 后重试 → 列表加载
      api.json('GET', '/api/v1/characters',
          {'characters': [_character()]});
      await tester.tap(find.text(l10n.retry));
      await tester.pumpAndSettle();
      expect(find.text('Alpha'), findsOneWidget);
    });
  });

  group('Phone desktop', () {
    // 注意：桌面 push 后外页 offstage，l10n 须在 tap 前取
    Future<void> openDesktop(WidgetTester tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();
    }

    AppLocalizations l10nBeforeOpen(WidgetTester tester) {
      final l10n = l10nOf(tester);
      return l10n;
    }

    testWidgets('clock/apps/edit render; single BackdropFilter on clock',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nBeforeOpen(tester);
      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();

      // 时钟（HH:mm）+ 日期行存在；应用 label 与编辑按钮
      expect(find.textContaining(RegExp(r'^\d{2}:\d{2}$')), findsWidgets);
      expect(find.text(l10n.appChat), findsOneWidget);
      expect(find.text(l10n.appAlbum), findsOneWidget);
      expect(find.text(l10n.edit), findsOneWidget);
      // 整桌面仅时钟卡 1 个 BackdropFilter
      expect(find.byType(BackdropFilter), findsOneWidget);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('light http wallpaper: clock renders readable, no crash',
        (tester) async {
      // 自定义浅色 http 壁纸：走 Image.network 分支（测试内 400 → errorBuilder 深色渐变兜底），
      // 覆盖 2.3 时钟卡黑色 scrim 兜底：断言时钟文本可见 + scrim 存在且不崩。
      api.json('GET', '/api/v1/phone-desktop/layouts', {
        'apps': [],
        'catalog': [
          {'key': 'chat'},
          {'key': 'album'},
          {'key': 'browser'},
          {'key': 'theme'},
        ],
        'browser_plugin_enabled': true,
        'wallpaper': 'http://127.0.0.1:9/wallpapers/light.jpg',
      });
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();

      // 时钟（HH:mm）可见，http 壁纸走 Image.network
      expect(find.textContaining(RegExp(r'^\d{2}:\d{2}$')), findsWidgets);
      expect(find.byType(Image), findsWidgets);
      // 2.3 scrim 兜底：黑色 0.20 遮罩存在
      final scrim = find.byWidgetPredicate((w) =>
          w is DecoratedBox &&
          w.decoration is BoxDecoration &&
          ((w.decoration as BoxDecoration).color ?? Colors.transparent) ==
              Colors.black.withValues(alpha: 0.20));
      expect(scrim, findsOneWidget);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('edit mode: done + delete badge appear; done exits',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nBeforeOpen(tester);
      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();

      await tester.tap(find.text(l10n.edit));
      await tester.pumpAndSettle();
      expect(find.text(l10n.done), findsOneWidget);
      // browser 应用 deletable=true → 编辑模式出现红圆 × 角标
      expect(find.byIcon(Icons.close), findsOneWidget);

      await tester.tap(find.text(l10n.done));
      await tester.pumpAndSettle();
      expect(find.text(l10n.edit), findsOneWidget);
      expect(find.byIcon(Icons.close), findsNothing);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('icon press scale key present by default, absent under reduceMotion',
        (tester) async {
      await openDesktop(tester);
      expect(find.byKey(const Key('iconPressScale')), findsWidgets);
      await tester.pumpWidget(const SizedBox());

      await settings.setReduceMotion(true);
      await openDesktop(tester);
      expect(find.byKey(const Key('iconPressScale')), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('Privacy lock', () {
    testWidgets('locked character opens PrivacyLockView with pulsing container',
        (tester) async {
      api.json('GET', '/api/v1/privacy/1/status',
          {'enabled': true, 'locked': true, 'cooldown_remaining': 0});
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      await tester.tap(find.text('Alpha'));
      // 脉冲是持续循环动画，pumpAndSettle 永不收敛 → 固定多帧 pump 等待异步隐私检查 + 路由转场
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 100));
      }

      expect(find.byType(PrivacyLockView), findsOneWidget);
      // 渐变锁容器 + 脉冲动画默认存在
      expect(find.byKey(const Key('privacyPulse')), findsOneWidget);
      expect(find.byKey(const Key('privacyPulseAnim')), findsOneWidget);
      expect(find.text(l10n.privacyApplyButton), findsOneWidget);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('reduceMotion=true: pulse animation absent', (tester) async {
      await settings.setReduceMotion(true);
      api.json('GET', '/api/v1/privacy/1/status',
          {'enabled': true, 'locked': true, 'cooldown_remaining': 0});
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await tester.tap(find.text('Alpha'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('privacyPulse')), findsOneWidget);
      // 无脉冲：容器外无 AnimatedBuilder
      expect(find.byKey(const Key('privacyPulseAnim')), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });
  });
}

Map<String, Object?> _character() => {
      'id': 1,
      'name': 'Alpha',
      'personality': 'kind and curious',
    };

Map<String, Object?> _chat() => {
      'id': 1,
      'character_a_id': 1,
      'character_a_name': 'Alpha',
      'character_b_id': 2,
      'character_b_name': 'Beta',
      'speaker_id': 1,
      'speaker_name': 'Alpha',
      'round_seq': 1,
      'content': 'last message text',
      'created_at': '2026-08-28T02:00:00Z',
    };

void _mockDefault(FakeApiAdapter api) {
  api.json('GET', '/api/v1/characters', {'characters': [_character()]});
  api.json('GET', '/api/v1/ai-chats', {'items': [_chat()]});
  api.json('GET', '/api/v1/auth/profile', {'ai_social_enabled': true});
  api.json('GET', '/api/v1/life/state', {'phase': 'morning'});
  api.json('GET', '/api/v1/characters/1/states',
      {'character_id': 1, 'mood': 80, 'anger': 5, 'fatigue': 10});
  api.json('GET', '/api/v1/privacy/1/status', {'enabled': false});
  api.json('GET', '/api/v1/phone-desktop/layouts', {
    'apps': [],
    'catalog': [
      {'key': 'chat'},
      {'key': 'album'},
      {'key': 'browser'},
      {'key': 'theme'},
    ],
    'browser_plugin_enabled': true,
    'wallpaper': null,
  });
  // weather 404 → 静默
}
