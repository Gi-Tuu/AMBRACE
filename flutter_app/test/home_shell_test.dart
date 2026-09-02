import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/pets_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/features/home/home_screen.dart';
import 'package:ai_companion/features/social/moments_screen.dart';
import 'package:ai_companion/theme/app_theme.dart';
import 'package:ai_companion/widgets/home_bottom_bar.dart';
import 'package:ai_companion/widgets/home_drawer.dart';

/// Aurora Phase 2 B1 home shell tests:
/// glass capsule bottom bar (4 tabs / tap switch / light+dark / glass skin
/// BackdropFilter), drawer open/close (reusing AppDrawerController), and
/// moments badge pulse disabled under reduceMotion.
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  Widget l10nApp({required Widget home, ThemeData? theme}) => MaterialApp(
        locale: const Locale('zh'),
        theme: theme,
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: home,
      );

  HomeBottomBar bar({int selected = 0, ValueChanged<int>? onSelected}) =>
      HomeBottomBar(
        selectedIndex: selected,
        onSelected: onSelected ?? (_) {},
        items: const [
          HomeBottomBarItem(icon: Icon(Icons.people), label: 'tab0'),
          HomeBottomBarItem(icon: Icon(Icons.favorite), label: 'tab1'),
          HomeBottomBarItem(icon: Icon(Icons.forum_outlined), label: 'tab2'),
          HomeBottomBarItem(icon: Icon(Icons.home_outlined), label: 'tab3'),
        ],
      );

  /// Full home shell with the minimal provider set.
  Widget shell() => MultiProvider(
        providers: [
          ChangeNotifierProvider(create: (_) => SettingsProvider()),
          ChangeNotifierProvider(create: (_) => PetsProvider()),
        ],
        child: l10nApp(home: const HomeScreen()),
      );

  group('HomeBottomBar', () {
    testWidgets('renders 4 tabs and reports tap index', (tester) async {
      int selected = -1;
      await tester.pumpWidget(l10nApp(
        home: Scaffold(
          body: const SizedBox.expand(),
          bottomNavigationBar: bar(
            selected: 0,
            onSelected: (i) => selected = i,
          ),
        ),
      ));

      expect(find.text('tab0'), findsOneWidget);
      expect(find.text('tab1'), findsOneWidget);
      expect(find.text('tab2'), findsOneWidget);
      expect(find.text('tab3'), findsOneWidget);

      await tester.tap(find.text('tab2'));
      expect(selected, 2);
      await tester.tap(find.text('tab3'));
      expect(selected, 3);
    });

    testWidgets('builds in light/dark and glass skin with BackdropFilter',
        (tester) async {
      // light (default ios skin)
      await tester.pumpWidget(l10nApp(
        theme: AppTheme.light(0),
        home: Scaffold(
          body: const SizedBox.expand(),
          bottomNavigationBar: bar(),
        ),
      ));
      await tester.pump();
      // #12：非 glass 皮肤（默认 ios）底栏不再做毛玻璃模糊
      expect(find.byType(BackdropFilter), findsNothing);

      // dark
      await tester.pumpWidget(l10nApp(
        theme: AppTheme.dark(0),
        home: Scaffold(
          body: const SizedBox.expand(),
          bottomNavigationBar: bar(),
        ),
      ));
      await tester.pump();
      expect(find.byType(BackdropFilter), findsNothing);

      // glass skin：必须同时把 SettingsProvider.skinId 置为 glass 才模糊
      final glassSettings = SettingsProvider();
      await glassSettings.setSkinId('glass');
      await tester.pumpWidget(MultiProvider(
        providers: [
          ChangeNotifierProvider<SettingsProvider>.value(value: glassSettings),
        ],
        child: l10nApp(
          theme: AppTheme.light(0, skinId: 'glass'),
          home: Scaffold(
            body: const SizedBox.expand(),
            bottomNavigationBar: bar(),
          ),
        ),
      ));
      await tester.pump();
      expect(find.byType(BackdropFilter), findsOneWidget);
    });

    testWidgets('selected item shows primary dot indicator', (tester) async {
      await tester.pumpWidget(l10nApp(
        home: Scaffold(
          body: const SizedBox.expand(),
          bottomNavigationBar: bar(selected: 1),
        ),
      ));
      await tester.pumpAndSettle();
      // Exactly one 4px dot container across the whole bar (on the selected item).
      final dots = find.byWidgetPredicate((w) =>
          w is AnimatedContainer &&
          w.constraints ==
              const BoxConstraints.tightFor(width: 4.0, height: 4.0));
      expect(dots, findsOneWidget);
    });
  });

  group('HomeScreen shell', () {
    testWidgets('4 tabs exist and tap switches page (PageView linkage)',
        (tester) async {
      await tester.pumpWidget(shell());
      await tester.pump(); // first frame + post-frame side effects
      await tester.pump(const Duration(milliseconds: 50));

      final ctx = tester.element(find.byType(HomeScreen));
      final l10n = AppLocalizations.of(ctx)!;
      final tabLabels = [
        l10n.tabFriends,
        l10n.tabMoments,
        l10n.tabAiInteraction,
        l10n.homeTitle,
      ];
      for (final label in tabLabels) {
        expect(find.text(label), findsOneWidget, reason: 'missing tab: $label');
      }

      // Tap tab 2 (moments) -> PageView shows MomentsScreen.
      // Scope note: tabs 3/4 are not switched to here — the home-visual page
      // contains out-of-scope pre-existing issues (a one-shot Timer never
      // cancelled, IosCardGroup ListTile debug assertion) that live outside
      // the files this task is allowed to touch.
      await tester.tap(find.text(tabLabels[1]));
      await tester.pumpAndSettle();
      expect(find.byType(MomentsScreen), findsOneWidget);

      // Back to tab 0 (friends).
      await tester.tap(find.text(tabLabels[0]));
      await tester.pumpAndSettle();
      expect(find.byType(MomentsScreen), findsNothing);

      // Dispose HomeScreen so its polling timers are cancelled.
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('Home drawer', () {
    testWidgets('opens and closes via AppDrawerController', (tester) async {
      await tester.pumpWidget(shell());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(AppDrawerController.isOpen.value, isFalse);

      AppDrawerController.open();
      await tester.pump(const Duration(milliseconds: 400));
      expect(AppDrawerController.isOpen.value, isTrue);
      expect(find.byType(HomeDrawer), findsOneWidget);
      expect(find.byKey(const Key('homeDrawerOverlay')), findsOneWidget);

      // Tap the scrim to close.
      await tester.tapAt(const Offset(700, 300));
      await tester.pump(const Duration(milliseconds: 400));
      expect(AppDrawerController.isOpen.value, isFalse);

      AppDrawerController.close();
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('drawer width is 76% of screen', (tester) async {
      await tester.pumpWidget(shell());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      AppDrawerController.open();
      await tester.pump(const Duration(milliseconds: 400));

      final drawerFinder = find.byType(HomeDrawer);
      expect(drawerFinder, findsOneWidget);
      final size = tester.getSize(drawerFinder);
      final screenWidth =
          tester.view.physicalSize.width / tester.view.devicePixelRatio;
      expect(size.width, closeTo(screenWidth * 0.76, 1.0));

      AppDrawerController.close();
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('Moments badge pulse', () {
    Widget badgeHost(SettingsProvider? settings, {bool disableAnimations = false}) {
      final app = MaterialApp(
        home: MediaQuery(
          data: MediaQueryData(disableAnimations: disableAnimations),
          child: Scaffold(
            body: Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: PulsingBadge(
                  child: Container(color: Colors.red),
                ),
              ),
            ),
          ),
        ),
      );
      if (settings == null) return app;
      return MultiProvider(
        providers: [ChangeNotifierProvider.value(value: settings)],
        child: app,
      );
    }

    testWidgets('default: breathing ring is rendered', (tester) async {
      await tester.pumpWidget(badgeHost(null));
      await tester.pump();
      expect(find.byKey(const Key('pulsingRing')), findsOneWidget);
      // Dispose to stop the repeating ticker before the test ends.
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('reduceMotion=true: no ring, static badge only', (tester) async {
      final s = SettingsProvider();
      await s.setReduceMotion(true);
      await tester.pumpWidget(badgeHost(s));
      await tester.pump();
      expect(find.byKey(const Key('pulsingRing')), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('system disableAnimations equals reduceMotion', (tester) async {
      await tester.pumpWidget(badgeHost(null, disableAnimations: true));
      await tester.pump();
      expect(find.byKey(const Key('pulsingRing')), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });
  });
}
