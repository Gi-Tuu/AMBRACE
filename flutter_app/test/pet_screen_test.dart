import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/pets_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/character/pet_screen.dart';
import 'package:ai_companion/services/api_client.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P4 宠物页测试：
/// 玻璃 AppBar、底部悬浮胶囊操作栏（feed/play/clean 走 _interact）、
/// 待机循环 reduceMotion 静态化、领养视图渲染。
Map<String, Object?> _petJson() => {
      'id': 1,
      'name': 'Mochi',
      'species': 'cat',
      'species_label': 'cat',
      'level': 3,
      'hunger': 80,
      'mood': 80,
      'energy': 80,
      'cleanliness': 80,
      'status_text': 'happy',
      'need_attention': false,
    };

void main() {
  late FakeApiAdapter api;
  late PetsProvider pets;
  late SettingsProvider settings;
  final postedActions = <String>[];

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    postedActions.clear();
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    settings = SettingsProvider();
    pets = PetsProvider();
    api.json('GET', '/api/v1/pets', {'pets': [_petJson()]});
    api.json('GET', '/api/v1/pets/ai-pets', {'items': []});
    for (final action in ['feed', 'play', 'clean']) {
      api.handle('POST', '/api/v1/pets/1/$action', (options) {
        postedActions.add(action);
        return FakeApiAdapter.body(_petJson(), 200);
      });
    }
  });

  tearDown(() {
    pets.dispose();
  });

  Widget app() => MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: pets),
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
          home: const PetScreen(),
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(PetScreen)))!;

  // 待机循环是无限重复动画，pumpAndSettle 永不收敛 → 固定 pump
  Future<void> pumpHome(WidgetTester tester) async {
    await tester.pumpWidget(app());
    await tester.pump(); // 首帧 + loadPets 触发
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pump(const Duration(milliseconds: 200));
  }

  group('Pet home', () {
    testWidgets('AppBar + pet name + stat bars render', (tester) async {
      await pumpHome(tester);
      final l10n = l10nOf(tester);

      expect(find.text(l10n.tabPets), findsOneWidget);
      expect(find.text('Mochi'), findsWidgets); // 名片 chip + 名称行
      expect(find.text(l10n.hunger), findsOneWidget);
      expect(find.text(l10n.mood), findsOneWidget);
      expect(find.text(l10n.energy), findsOneWidget);
      expect(find.text(l10n.cleanliness), findsOneWidget);
    });

    testWidgets('floating capsule: three action buttons with tooltips',
        (tester) async {
      await pumpHome(tester);
      final l10n = l10nOf(tester);

      expect(find.byTooltip(l10n.feed), findsOneWidget);
      expect(find.byTooltip(l10n.play), findsOneWidget);
      expect(find.byTooltip(l10n.clean), findsOneWidget);
    });

    testWidgets('tapping capsule buttons posts feed/play/clean', (tester) async {
      await pumpHome(tester);
      final l10n = l10nOf(tester);

      await tester.tap(find.byTooltip(l10n.feed));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(postedActions, contains('feed'));

      await tester.tap(find.byTooltip(l10n.play));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(postedActions, contains('play'));

      await tester.tap(find.byTooltip(l10n.clean));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(postedActions, contains('clean'));
    });

    testWidgets('idle animation: static under reduceMotion, moving by default',
        (tester) async {
      // reduceMotion：_idle 停止 → 2s 后 Transform 仍为静止帧（平移/旋转为 0）
      await settings.setReduceMotion(true);
      await pumpHome(tester);
      final transformFinder = find.descendant(
        of: find.byType(GestureDetector),
        matching: find.byType(Transform),
      ).first;
      await tester.pump(const Duration(seconds: 2));
      var m = tester.widget<Transform>(transformFinder).transform;
      expect(m.getTranslation().y, closeTo(0.0, 0.001),
          reason: 'reduceMotion should freeze idle motion');

      // 默认：待机循环运行 → 2s 后Transform 出现非零位移（呼吸/浮动）
      await settings.setReduceMotion(false);
      await tester.pumpWidget(app());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      final finder2 = find.descendant(
        of: find.byType(GestureDetector),
        matching: find.byType(Transform),
      ).first;
      await tester.pump(const Duration(seconds: 2));
      m = tester.widget<Transform>(finder2).transform;
      final moved = m.getTranslation().y.abs() > 0.001 ||
          (m.entry(0, 0) - 1.0).abs() > 0.001;
      expect(moved, isTrue, reason: 'default idle loop should animate');
    });

    testWidgets('adopt view renders without pets', (tester) async {
      api.json('GET', '/api/v1/pets', {'pets': []});
      await pumpHome(tester);
      final l10n = l10nOf(tester);

      expect(find.text(l10n.adoptHeading), findsOneWidget);
      expect(find.text(l10n.speciesCat), findsOneWidget);
    });
  });
}
