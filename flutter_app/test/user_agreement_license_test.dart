import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/auth/user_agreement_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 用户协议页与开源许可入口测试（AGPL-3.0-only，2026-10-13）。
///
/// 钉三件容易悄悄坏掉的事：
/// ① 中英两版协议都不许再出现硬编码的 "MIT License"，也不许把「禁止商用」写回来
///    （那句是对开源授权的减损，减损无效）；
/// ② 协议页的许可按钮要真的能打开系统 LicensePage，且两份资产能被 rootBundle 读到、
///    与仓库根的 LICENSE / NOTICE 逐字节一致 —— 资产没打进包时加载会失败而不是静默空页，
///    所以这里显式断言内容，不靠「页面打开了」蒙混过关；
/// ③ 许可注册**不阻塞首帧**：LicenseRegistry.addLicense 只是登记回调，资产读取发生在
///    用户真的打开许可页时（2026-09-13 黑屏修复的同一纪律）。
///
/// 不启动真实后台服务：只用 MaterialApp + 本地化代理，App 的网络层一概不碰。
/// 资产读取走 tester.runAsync —— widget 测试默认跑在 fake-async 里，rootBundle 的
/// 真实 IO 必须放进 runAsync 才推进得动（不这样做会 30s/10min 超时，不是断言失败）。
void main() {
  Widget app({required Locale locale, Widget? home}) => MaterialApp(
        locale: locale,
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: home ?? const UserAgreementScreen(),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(UserAgreementScreen)))!;

  Future<List<String>> loadLicenseAssets(WidgetTester tester) async {
    final agpl = await tester.runAsync(
        () => rootBundle.loadString('assets/legal/AGPL-3.0.txt'));
    final notice =
        await tester.runAsync(() => rootBundle.loadString('assets/legal/NOTICE.txt'));
    return <String>[agpl!, notice!];
  }

  /// 与 lib/main.dart 的 _registerProjectLicenses() 同形：只登记回调，不读资产。
  /// 这里把文本预先读好再喂进去，等价于用户真的打开许可页时回调里的那两次 loadString。
  void registerProjectLicenses(List<String> assets) {
    LicenseRegistry.addLicense(() async* {
      yield LicenseEntryWithLineBreaks(const ['AMBRACE (AGPL-3.0-only)'], assets[0]);
      yield LicenseEntryWithLineBreaks(const ['AMBRACE - NOTICE'], assets[1]);
    });
  }

  Future<void> pumpAgreement(WidgetTester tester, String locale) async {
    // 八节正文 + 按钮在 6000 逻辑像素内一次建全，避免 ListView 懒加载把后面的节漏掉
    tester.view.physicalSize = const Size(900, 6000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app(locale: Locale(locale)));
    await tester.pumpAndSettle();
  }

  group('协议页中英两版', () {
    for (final locale in ['zh', 'en']) {
      testWidgets('$locale: 八节全在、协议里不再有 MIT、也不禁商用', (tester) async {
        await pumpAgreement(tester, locale);
        final l10n = l10nOf(tester);

        final titles = <String>[
          l10n.agreeSection1Title,
          l10n.agreeSection2Title,
          l10n.agreeSection3Title,
          l10n.agreeSection4Title,
          l10n.agreeSection5Title,
          l10n.agreeSection6Title,
          l10n.agreeSection7Title,
          l10n.agreeSection8Title,
        ];
        expect(titles.where((t) => t.trim().isEmpty), isEmpty);
        for (final t in titles) {
          expect(find.text(t), findsOneWidget, reason: '章节「$t」缺失');
        }
        expect(find.text(l10n.agreeTitle), findsOneWidget);

        final full = '${l10n.agreeSection1Body}\n${l10n.agreeSection8Body}';
        expect(full, contains('AGPL-3.0-only'),
            reason: '$locale 协议没提 AGPL-3.0-only');
        expect(full, isNot(contains('MIT License')),
            reason: '$locale 协议还硬编码着 MIT');
        expect(full.toLowerCase(), isNot(contains('only for learning')),
            reason: '$locale 协议把禁商用写法带回来了');
        // 其余免责章节内容不许被这次改动顺带删掉
        expect(find.textContaining(l10n.agreeSection2Body.substring(0, 10)),
            findsWidgets);
      });
    }
  });

  testWidgets('协议页可滚动（正文长于视口时不会溢出）', (tester) async {
    tester.view.physicalSize = const Size(400, 700);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app(locale: const Locale('zh')));
    await tester.pumpAndSettle();

    expect(find.byType(ListView), findsOneWidget);
    expect(tester.takeException(), isNull, reason: '窄屏下协议页抛了布局异常');
    await tester.drag(find.byType(ListView), const Offset(0, -400));
    await tester.pumpAndSettle();
  });

  testWidgets('许可按钮可访问并打开系统 LicensePage', (tester) async {
    registerProjectLicenses(await loadLicenseAssets(tester));
    await pumpAgreement(tester, 'zh');
    final l10n = l10nOf(tester);

    final button = find.widgetWithText(TextButton, l10n.agreeViewLicense);
    expect(button, findsOneWidget, reason: '协议页没有「查看开源许可」按钮');
    expect(find.text(l10n.agreeViewLicenseHint), findsOneWidget);

    await tester.tap(button);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    expect(find.byType(LicensePage), findsOneWidget);
    expect(find.text('AMBRACE (AGPL-3.0-only)'), findsWidgets,
        reason: '许可页里没有项目自有的 AGPL 条目');
  });

  testWidgets('LicensePage 里读到的 AGPL 条文与 NOTICE 内容正确', (tester) async {
    final assets = await loadLicenseAssets(tester);
    registerProjectLicenses(assets);

    final entries =
        (await tester.runAsync(() => LicenseRegistry.licenses.toList()))!;
    expect(entries, isNotEmpty, reason: '许可注册表是空的');
    expect(entries.map((e) => e.packages.join()).toList(),
        contains('AMBRACE (AGPL-3.0-only)'));
    String textOf(LicenseEntry e) => (e as LicenseEntryWithLineBreaks).text;
    expect(
      textOf(entries.firstWhere((e) => e.packages.join() == 'AMBRACE (AGPL-3.0-only)')),
      contains('GNU AFFERO GENERAL PUBLIC LICENSE'),
    );
    expect(
      textOf(entries.firstWhere((e) => e.packages.join() == 'AMBRACE - NOTICE')),
      allOf(contains('does not prohibit commercial use'), contains('3.5.3')),
    );
  });

  testWidgets('许可注册不阻塞首帧（资产读取推迟到打开许可页时）', (tester) async {
    // 回调体里要读真实资产，故意不预先加载：注册本身必须同步返回、首帧照常出
    LicenseRegistry.addLicense(() async* {
      final agpl = await rootBundle.loadString('assets/legal/AGPL-3.0.txt');
      yield LicenseEntryWithLineBreaks(const ['AMBRACE (AGPL-3.0-only)'], agpl);
    });

    await tester.pumpWidget(app(
      locale: const Locale('zh'),
      home: const Scaffold(body: Center(child: Text('first frame'))),
    ));
    await tester.pump();
    expect(find.text('first frame'), findsOneWidget,
        reason: '许可注册把首帧挡住了');
    expect(tester.takeException(), isNull);

    // 真去收集许可时才发生资产读取，且能读到 → 推迟发生 ≠ 永远不发生
    final entries =
        (await tester.runAsync(() => LicenseRegistry.licenses.toList()))!;
    expect(entries.map((e) => e.packages.join()), contains('AMBRACE (AGPL-3.0-only)'));
  });

  testWidgets('两份资产与仓库根文件逐字节一致（App 内许可页不会读到过期版本）',
      (tester) async {
    final assets = await loadLicenseAssets(tester);
    expect(assets[0], startsWith('                    GNU AFFERO GENERAL PUBLIC LICENSE'));
    expect(assets[0], contains('Version 3, 19 November 2007'));
    expect(assets[1], contains('AGPL-3.0-only'));
    expect(assets[1], contains('3.5.3'));
    expect(assets[1], contains('does not prohibit commercial use'));

    final repoRoot = Directory.current.parent;
    expect(assets[0], File('${repoRoot.path}/LICENSE').readAsStringSync(),
        reason: 'assets/legal/AGPL-3.0.txt 与仓库根 LICENSE 不一致');
    expect(assets[1], File('${repoRoot.path}/NOTICE').readAsStringSync(),
        reason: 'assets/legal/NOTICE.txt 与仓库根 NOTICE 不一致');
  });
}