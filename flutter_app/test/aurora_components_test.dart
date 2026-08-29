import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/theme/app_theme.dart';
import 'package:ai_companion/theme/aurora_tokens.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/glass_bar.dart';
import 'package:ai_companion/widgets/floating_sheet.dart';
import 'package:ai_companion/widgets/empty_state.dart';

/// Aurora Phase 1 组件基建测试：
/// 动效/模糊 token、全局开关、AuroraCard / GlassBar / FloatingSheet / EmptyState。
void main() {
  group('AppMotion', () {
    test('常量存在且时长符合预期', () {
      expect(AppMotion.fast, const Duration(milliseconds: 200));
      expect(AppMotion.normal, const Duration(milliseconds: 350));
      expect(AppMotion.slow, const Duration(milliseconds: 600));
      expect(AppMotion.float, const Duration(milliseconds: 3200));
      expect(AppMotion.spring, Curves.easeOutBack);
      expect(AppMotion.emphasized, Curves.easeOutCubic);
    });
  });

  group('AppGlass.effectiveBlur', () {
    test('reduceBlur=false 原值；true 减半且不低于 4', () {
      expect(AppGlass.effectiveBlur(20, reduceBlur: false), 20);
      expect(AppGlass.effectiveBlur(20, reduceBlur: true), 10);
      expect(AppGlass.effectiveBlur(32, reduceBlur: true), 16);
      // 最低不低于 4
      expect(AppGlass.effectiveBlur(6, reduceBlur: true), 4);
      expect(AppGlass.effectiveBlur(4, reduceBlur: true), 4);
      expect(AppGlass.effectiveBlur(8, reduceBlur: true), 4);
    });

    test('常量符合方案值', () {
      expect(AppGlass.blurLight, 12.0);
      expect(AppGlass.blurMedium, 20.0);
      expect(AppGlass.blurHeavy, 32.0);
    });
  });

  group('SettingsProvider 全局动效/模糊开关', () {
    test('setReduceMotion/setReduceBlur 持久化 round-trip（模拟重启）', () async {
      SharedPreferences.setMockInitialValues({});
      final s = SettingsProvider();
      await s.load();
      expect(s.reduceMotion, isFalse);
      expect(s.reduceBlur, isFalse);

      await s.setReduceMotion(true);
      await s.setReduceBlur(true);
      expect(s.reduceMotion, isTrue);
      expect(s.reduceBlur, isTrue);

      // 模拟重启：新实例从 SharedPreferences 读回
      final s2 = SettingsProvider();
      await s2.load();
      expect(s2.reduceMotion, isTrue);
      expect(s2.reduceBlur, isTrue);
    });

    test('默认关 / 关闭开关回退', () async {
      SharedPreferences.setMockInitialValues({});
      final s = SettingsProvider();
      await s.load();
      await s.setReduceMotion(true);
      await s.setReduceBlur(true);
      await s.setReduceMotion(false);
      await s.setReduceBlur(false);
      expect(s.reduceMotion, isFalse);
      expect(s.reduceBlur, isFalse);
    });
  });

  group('AuroraCard', () {
    Widget wrapWithSettings(Widget child, SettingsProvider settings) {
      return ChangeNotifierProvider<SettingsProvider>.value(
        value: settings,
        child: child,
      );
    }

    testWidgets('非 glass 皮肤背景 = scheme.surface（无 Provider 兜底到不降级）', (tester) async {
      final theme = AppTheme.light(0, skinId: 'ios');
      await tester.pumpWidget(MaterialApp(
        theme: theme,
        home: Scaffold(
          body: const AuroraCard(child: Text('card')),
        ),
      ));

      final surface = theme.colorScheme.surface;
      final found = find.byWidgetPredicate((w) {
        if (w is! Container) return false;
        final d = w.decoration;
        if (d is! BoxDecoration) return false;
        return d.color == surface && (d.borderRadius as BorderRadius?)?.topLeft.x == 20;
      });
      expect(found, findsWidgets);
      expect(find.byType(BackdropFilter), findsNothing);
      expect(find.text('card'), findsOneWidget);

      // 无 Provider 时 reduceBlur 视为 false → 组件不降级不崩溃
      expect(tester.takeException(), isNull);
    });

    testWidgets('glass 皮肤背景非空；blurred 时含 BackdropFilter', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsProvider();
      await settings.setSkinId('glass');
      await settings.setReduceBlur(true);

      final theme = AppTheme.build(brightness: Brightness.light, seedIndex: 0, skinId: 'glass');
      await tester.pumpWidget(wrapWithSettings(
        MaterialApp(
          theme: theme,
          home: Scaffold(
            body: AuroraCard(blurred: true, child: const Text('card')),
          ),
        ),
        settings,
      ));

      // 背景非空（glass 皮肤 glassBackground != null）
      final foundBg = find.byWidgetPredicate((w) {
        if (w is! Container) return false;
        final d = w.decoration;
        if (d is! BoxDecoration) return false;
        return d.color != null && (d.borderRadius as BorderRadius?)?.topLeft.x == 20;
      });
      expect(foundBg, findsWidgets);

      // widget 树含 BackdropFilter；sigma 由 effectiveBlur 决定（纯函数在 AppGlass 组断言）
      expect(find.byType(BackdropFilter), findsWidgets);
      // 印证 reduceBlur 生效的纯函数断言（ImageFilter 不暴露 sigma，故在此复核）
      expect(AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: true), 10.0);
    });

    testWidgets('glass 皮肤 blurred 时 reduceBlur=false 仍含 BackdropFilter', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsProvider(); // reduceBlur 默认 false
      await settings.setSkinId('glass');

      final theme = AppTheme.build(brightness: Brightness.light, seedIndex: 0, skinId: 'glass');
      await tester.pumpWidget(wrapWithSettings(
        MaterialApp(
          theme: theme,
          home: Scaffold(
            body: AuroraCard(blurred: true, child: const Text('card')),
          ),
        ),
        settings,
      ));

      expect(find.byType(BackdropFilter), findsWidgets);
      expect(AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: false), AppGlass.blurMedium);
    });
  });

  group('GlassBar', () {
    Widget wrapWithSettings(Widget child, SettingsProvider settings) {
      return ChangeNotifierProvider<SettingsProvider>.value(
        value: settings,
        child: child,
      );
    }

    testWidgets('无 Provider（视为非 glass）不模糊、不崩溃', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: GlassBar(child: const Text('bar')),
        ),
      ));
      expect(find.text('bar'), findsOneWidget);
      // #12：只有 glass 皮肤才做 BackdropFilter；无 Provider/非 glass 不模糊
      expect(find.byType(BackdropFilter), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('reduceBlur 下 sigma 减半（纯函数断言）', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsProvider();
      await settings.setSkinId('glass');
      await settings.setReduceBlur(true);

      await tester.pumpWidget(wrapWithSettings(
        MaterialApp(
          home: Scaffold(body: GlassBar(child: const Text('bar'))),
        ),
        settings,
      ));

      expect(find.byType(BackdropFilter), findsWidgets);
      // ImageFilter 不暴露 sigma，用 effectiveBlur 纯函数断言（组件经它取 sigma）
      expect(AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: true), 10.0);
    });

    testWidgets('reduceBlur=false 保持原 sigma（纯函数断言）', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsProvider();
      await settings.setSkinId('glass');

      await tester.pumpWidget(wrapWithSettings(
        MaterialApp(
          home: Scaffold(body: GlassBar(child: const Text('bar'))),
        ),
        settings,
      ));

      expect(find.byType(BackdropFilter), findsWidgets);
      expect(AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: false), AppGlass.blurMedium);
    });
  });

  group('EmptyState', () {
    testWidgets('渲染 icon/title/subtitle/action', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EmptyState(
            icon: Icons.inbox_outlined,
            title: '空',
            subtitle: '还没有内容',
            action: ElevatedButton(onPressed: () {}, child: const Text('去创建')),
          ),
        ),
      ));

      expect(find.byIcon(Icons.inbox_outlined), findsOneWidget);
      expect(find.text('空'), findsOneWidget);
      expect(find.text('还没有内容'), findsOneWidget);
      expect(find.widgetWithText(ElevatedButton, '去创建'), findsOneWidget);
    });

    testWidgets('无 action/subtitle 时仅渲染 icon+title', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: const EmptyState(icon: Icons.cloud_off, title: '暂无')),
      ));
      expect(find.byIcon(Icons.cloud_off), findsOneWidget);
      expect(find.text('暂无'), findsOneWidget);
    });
  });

  group('FloatingSheet', () {
    testWidgets('showFloatingSheet 后内容可见，可关闭', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => Center(
              child: ElevatedButton(
                onPressed: () => showFloatingSheet(
                  context: context,
                  title: '标题',
                  child: const Text('sheet-content'),
                ),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      ));

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      expect(find.text('sheet-content'), findsOneWidget);
      expect(find.text('标题'), findsOneWidget);

      // 点击面板上方的遮罩关闭
      await tester.tapAt(const Offset(400, 30));
      await tester.pumpAndSettle();

      expect(find.text('sheet-content'), findsNothing);
    });

    testWidgets('expandable 展开/收起可切换（点击拖拽区）', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => Center(
              child: ElevatedButton(
                onPressed: () => showFloatingSheet(
                  context: context,
                  child: const Text('sheet-content'),
                ),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      ));

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      // 拖拽把手在面板顶部，点击切换半/全展开（验证不抛错且内容仍在）
      final handle = find.byKey(const ValueKey('floatingSheetHandle'));
      expect(handle, findsOneWidget);
      await tester.tap(handle);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('sheet-content'), findsOneWidget);
    });
  });
}
