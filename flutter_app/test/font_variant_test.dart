import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/settings/appearance_screen.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/theme/app_theme.dart';
import 'package:ai_companion/theme/font_variant.dart';

/// R8（工具轨迹治理批次三）：正文字体三档（跟随系统/衬线/圆润）。
/// 默认 system=跟随系统=与治理前行为一致（零回归）；serif/rounded 应用到全局
/// TextTheme；aegean 装饰字体不被覆盖；SharedPreferences 键 font_variant 持久化。
void main() {
  test('resolveFontFamily：system=null（跟随系统默认）', () {
    expect(resolveFontFamily(FontVariant.system), isNull);
  });

  test('resolveFontFamily：serif=系统衬线逻辑字体', () {
    expect(resolveFontFamily(FontVariant.serif), 'serif');
  });

  test('resolveFontFamily：rounded——iOS 回退 null，其余平台 sans-serif-rounded', () {
    if (Platform.isIOS) {
      expect(resolveFontFamily(FontVariant.rounded), isNull);
    } else {
      expect(resolveFontFamily(FontVariant.rounded), 'sans-serif-rounded');
    }
  });

  test('AppTheme：默认 system 与治理前行为一致（fontFamily 未覆写）', () {
    final legacy = AppTheme.light(0);
    final now = AppTheme.light(0, fontVariant: FontVariant.system);
    final ff = now.textTheme.bodyMedium?.fontFamily;
    expect(ff, legacy.textTheme.bodyMedium?.fontFamily);
    expect(ff, isNot('serif'), reason: 'system 档不注入 serif/rounded 逻辑族名');
  });

  test('AppTheme：serif 档全局正文/主文本应用衬线', () {
    final t = AppTheme.light(0, fontVariant: FontVariant.serif);
    expect(t.textTheme.bodyMedium?.fontFamily, 'serif');
    expect(t.primaryTextTheme.bodyMedium?.fontFamily, 'serif');
    expect(t.textTheme.titleLarge?.fontFamily, 'serif');
  });

  test('AppTheme：aegean 皮肤不被全局正文字体覆盖（装饰字体保留）', () {
    final t = AppTheme.light(0, skinId: 'aegean', fontVariant: FontVariant.serif);
    // aegean 自带花体 textTheme；serif 档不注入 'serif' 逻辑族名
    expect(t.textTheme.bodyMedium?.fontFamily, isNot('serif'));
  });

  group('SettingsProvider 持久化（键 font_variant）', () {
    test('缺省 = system（零回归）', () async {
      SharedPreferences.setMockInitialValues({});
      final p = SettingsProvider();
      await p.load();
      expect(p.fontVariant, FontVariant.system);
    });

    test('读取存量值 + 越界回退 system', () async {
      SharedPreferences.setMockInitialValues({'font_variant': 1});
      final p = SettingsProvider();
      await p.load();
      expect(p.fontVariant, FontVariant.serif);

      SharedPreferences.setMockInitialValues({'font_variant': 99});
      final p2 = SettingsProvider();
      await p2.load();
      expect(p2.fontVariant, FontVariant.system);
    });

    test('setFontVariant 持久化 + 通知监听者', () async {
      SharedPreferences.setMockInitialValues({});
      final p = SettingsProvider();
      await p.load();
      var notified = 0;
      p.addListener(() => notified++);
      await p.setFontVariant(FontVariant.rounded);
      expect(p.fontVariant, FontVariant.rounded);
      expect(notified, greaterThan(0));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getInt('font_variant'), FontVariant.rounded.index);
    });
  });

  testWidgets('外观页：正文字体三选一展示 + 点击切换', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final settings = SettingsProvider();
    await settings.load();

    await tester.pumpWidget(ChangeNotifierProvider.value(
      value: settings,
      child: MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: const AppearanceScreen(),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('正文字体'), findsOneWidget);
    // 「跟随系统」与主题模式分区共用文案（≥2 处），衬线/圆润为本分区独有
    expect(find.text('跟随系统'), findsWidgets);
    expect(find.text('衬线体'), findsOneWidget);
    expect(find.text('圆润体'), findsOneWidget);
    // 限制说明文案（如实注明厂商第三方字体可能不生效）
    expect(find.textContaining('主题商店'), findsOneWidget);

    await tester.tap(find.text('衬线体'));
    await tester.pumpAndSettle();
    expect(settings.fontVariant, FontVariant.serif);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getInt('font_variant'), 1);

    // 回到跟随系统（最后一个是本字体分区的 segment）
    await tester.tap(find.text('跟随系统').last);
    await tester.pumpAndSettle();
    expect(settings.fontVariant, FontVariant.system);
    expect(prefs.getInt('font_variant'), 0);
  });
}
