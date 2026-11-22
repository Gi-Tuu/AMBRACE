// 外观批（2026-11-22）守卫：皮肤预设/主题色构建器（单元）＋外观卡交互（Widget）。
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/settings_pane.dart';
import 'package:ambrace_pwa/l10n/reader_strings.dart';
import 'package:ambrace_pwa/theme/pwa_skins.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _NoNetAdapter implements HttpClientAdapter {
  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
    // 设置页会拉 profile/configs：合成最小应答；本组用例不依赖其内容。
    return ResponseBody.fromString(
        o.path == '/api/v1/llm-configs'
            ? '{"items":[]}'
            : (o.path == '/api/v1/system/llm-usage'
                ? '{"today":0}'
                : '{"id":4}'),
        200,
        headers: {Headers.contentTypeHeader: ['application/json']});
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  test('pwaWorkbenchTheme：皮肤三色板生效；主题色覆盖 seed', () {
    final paper = pwaSkinById('paper');
    final light = pwaWorkbenchTheme(Brightness.light, paper);
    expect(light.scaffoldBackgroundColor, paper.canvasLight);
    expect(light.cardTheme.color, paper.panelLight);

    final dark = pwaWorkbenchTheme(Brightness.dark, paper);
    expect(dark.scaffoldBackgroundColor, paper.canvasDark);

    // 主题色覆盖 seed：primary 随 accent 变（skin.seed ≠ accent）
    final base = pwaWorkbenchTheme(Brightness.light, paper);
    final accent = pwaWorkbenchTheme(Brightness.light, paper,
        accentSeed: const Color(0xFF2196F3));
    expect(accent.colorScheme.primary, isNot(base.colorScheme.primary));
    // ios 预设原值覆写：导航栏底色保持 F0EDE6（默认观感不漂）
    final ios = pwaWorkbenchTheme(Brightness.light, pwaSkinById('ios'));
    expect(ios.navigationRailTheme.backgroundColor, const Color(0xFFF0EDE6));
  });

  testWidgets('外观卡：三段模式＋皮肤 Chips＋主题色圆点回调', (tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final client =
        AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
    client.dio.httpClientAdapter = _NoNetAdapter();

    ThemeMode? pickedMode;
    String? pickedSkin;
    int? pickedSeed;
    await tester.pumpWidget(MaterialApp(
      home: SettingsPane(
        client: client,
        myUserId: 4,
        lang: 'zh',
        themeMode: ThemeMode.light,
        onTheme: (m) => pickedMode = m,
        onLang: (_) {},
        onLogout: () {},
        rs: const ReaderStrings('zh'),
        cs: const CoreStrings('zh'),
        skinId: 'ios',
        onSkin: (id) => pickedSkin = id,
        seedIndex: -1,
        onSeed: (i) => pickedSeed = i,
      ),
    ));
    await tester.pumpAndSettle();

    // 三段模式：点「跟随系统」→ ThemeMode.system
    await tester.tap(find.text('跟随系统'));
    await tester.pumpAndSettle();
    expect(pickedMode, ThemeMode.system);

    // 皮肤 Chips：7 款齐全，点「纸色」→ onSkin('paper')
    expect(find.text('纸色'), findsOneWidget);
    expect(find.text('霓虹'), findsOneWidget);
    expect(find.text('爱琴海'), findsOneWidget);
    await tester.tap(find.text('纸色'));
    await tester.pumpAndSettle();
    expect(pickedSkin, 'paper');

    // 主题色：跟随皮肤选中态 + 6 色点，点「青」→ onSeed(3)
    expect(find.text('跟随皮肤'), findsOneWidget);
    await tester.tap(find.text('青'));
    await tester.pumpAndSettle();
    expect(pickedSeed, 3);
  });
}
