import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/theme/app_theme.dart';
import 'package:ai_companion/theme/skins/skin_colors.dart';
import 'package:ai_companion/theme/skins/skin_registry.dart';
import 'package:ai_companion/widgets/message_bubble.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

void main() {
  setUp(() {
    // 每个测试前重置注册表，确保干净状态
    SkinRegistry.initialize();
  });

  group('SkinRegistry', () {
    test('initialize 注册 5 款内置皮肤', () {
      expect(SkinRegistry.all.length, 5);
      expect(SkinRegistry.ids, containsAll(['ios', 'warm', 'material', 'paper', 'neon']));
    });

    test('get 未知 id 回退到默认 ios', () {
      final skin = SkinRegistry.get('nonexistent');
      expect(skin.id, 'ios');
    });

    test('defaultSkinId 是 ios', () {
      expect(SkinRegistry.defaultSkinId, 'ios');
    });
  });

  group('AppTheme 皮肤构建', () {
    test('light/dark 接受 skinId 参数并注入 SkinColors', () {
      final light = AppTheme.light(0, skinId: 'paper');
      final dark = AppTheme.dark(0, skinId: 'neon');

      expect(light.extension<SkinColors>(), isNotNull);
      expect(dark.extension<SkinColors>(), isNotNull);
    });

    test('paper 皮肤不支持深色时，dark() 回退到 ios', () {
      final darkTheme = AppTheme.dark(0, skinId: 'paper');
      final iosDark = AppTheme.dark(0, skinId: 'ios');

      // paper 深色回退后，scaffoldBackgroundColor 应与 ios 深色一致
      expect(darkTheme.scaffoldBackgroundColor, iosDark.scaffoldBackgroundColor);
    });

    test('paper 皮肤浅色正常生效（不回退）', () {
      final paperLight = AppTheme.light(0, skinId: 'paper');
      // paper 浅色背景是 0xFFFDF8F0
      expect(paperLight.scaffoldBackgroundColor, const Color(0xFFFDF8F0));
    });

    test('neon 皮肤深色正常生效', () {
      final neonDark = AppTheme.dark(0, skinId: 'neon');
      expect(neonDark.scaffoldBackgroundColor, const Color(0xFF050508));
    });

    test('不同 seedColorIndex 影响用户气泡色', () {
      final blueTheme = AppTheme.light(0, skinId: 'ios');
      final pinkTheme = AppTheme.light(2, skinId: 'ios');

      final blueColors = blueTheme.extension<SkinColors>()!;
      final pinkColors = pinkTheme.extension<SkinColors>()!;

      // ios 皮肤用户气泡 = seedColor，不同 index 颜色不同
      expect(blueColors.bubbleUser, isNot(pinkColors.bubbleUser));
    });
  });

  group('SkinColors 扩展', () {
    test('material 皮肤 SkinColors 全 null（走 ColorScheme 兜底）', () {
      final theme = AppTheme.light(0, skinId: 'material');
      final colors = theme.extension<SkinColors>()!;
      expect(colors.bubbleUser, isNull);
      expect(colors.bubbleAi, isNull);
      expect(colors.inputBarBg, isNull);
    });

    test('ios 皮肤 SkinColors 有完整气泡色', () {
      final theme = AppTheme.light(0, skinId: 'ios');
      final colors = theme.extension<SkinColors>()!;
      expect(colors.bubbleUser, isNotNull);
      expect(colors.bubbleAi, isNotNull);
      expect(colors.bubbleUserText, isNotNull);
      expect(colors.bubbleAiText, isNotNull);
      expect(colors.inputBarBg, isNotNull);
    });
  });

  group('MessageBubble 皮肤消费', () {
    Widget wrapWithSkin(Widget child, String skinId, {Brightness brightness = Brightness.light}) {
      final theme = brightness == Brightness.dark
          ? AppTheme.dark(0, skinId: skinId)
          : AppTheme.light(0, skinId: skinId);
      return MaterialApp(
        theme: theme,
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(body: child),
      );
    }

    testWidgets('paper 皮肤下 AI 气泡背景为白色', (WidgetTester tester) async {
      await tester.pumpWidget(wrapWithSkin(
        const MessageBubble(message: '你好', isUser: false),
        'paper',
      ));
      await tester.pump();

      // 找到气泡 Container（paper AI 气泡色 = 白色）
      final containerFinder = find.byWidgetPredicate(
        (w) => w is Container && w.decoration is BoxDecoration,
      );
      bool foundPaperBubble = false;
      for (final widget in containerFinder.evaluate()) {
        final container = widget.widget as Container;
        final deco = container.decoration as BoxDecoration?;
        if (deco?.color == Colors.white) {
          foundPaperBubble = true;
          break;
        }
      }
      expect(foundPaperBubble, isTrue);
    });

    testWidgets('ios 皮肤下用户气泡有颜色（非透明）', (WidgetTester tester) async {
      await tester.pumpWidget(wrapWithSkin(
        const MessageBubble(message: '在吗', isUser: true),
        'ios',
      ));
      await tester.pump();

      final containerFinder = find.byWidgetPredicate(
        (w) => w is Container && w.decoration is BoxDecoration,
      );
      bool foundColoredBubble = false;
      for (final widget in containerFinder.evaluate()) {
        final container = widget.widget as Container;
        final deco = container.decoration as BoxDecoration?;
        if (deco?.color != null && deco!.color!.a > 0 && deco.color != Colors.white) {
          foundColoredBubble = true;
          break;
        }
      }
      expect(foundColoredBubble, isTrue);
    });
  });
}
