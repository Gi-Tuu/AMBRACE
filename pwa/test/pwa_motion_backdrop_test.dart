// 装饰动效层守卫（2026-11-24）：皮肤底色/光晕、减少动态效果、静态皮肤不注册 Ticker。
import 'package:ambrace_pwa/theme/pwa_backdrop.dart';
import 'package:ambrace_pwa/theme/pwa_skins.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _host(String skin, {bool reduce = false}) {
  final preset = pwaSkinById(skin);
  return MaterialApp(
    theme: pwaWorkbenchTheme(Brightness.light, preset),
    home: Builder(builder: (context) {
      return MediaQuery(
        data: MediaQuery.of(context).copyWith(disableAnimations: reduce),
        child: PwaBackdrop(
          skinId: skin,
          child: const Scaffold(body: Center(child: Text('内容'))),
        ),
      );
    }),
  );
}

PwaBackdropState _state(WidgetTester tester) =>
    tester.state<PwaBackdropState>(find.byType(PwaBackdrop));

void main() {
  testWidgets('动效皮肤：默认持续播放，底色＋两层光晕都在', (tester) async {
    await tester.pumpWidget(_host('glass'));
    expect(_state(tester).isTicking, isTrue);
    expect(find.text('内容'), findsOneWidget);
    // 底色渐变 1 层 ＋ 两处径向光晕
    expect(find.byType(DecoratedBox), findsAtLeastNWidgets(3));
  });

  testWidgets('系统「减少动态效果」：停在静止帧，不注册 Ticker', (tester) async {
    await tester.pumpWidget(_host('ios', reduce: true));
    expect(_state(tester).isTicking, isFalse);
  });

  testWidgets('静态皮肤（paper）：即使允许动效也不播放', (tester) async {
    await tester.pumpWidget(_host('paper'));
    expect(_state(tester).isTicking, isFalse);
  });

  testWidgets('换肤：动效皮肤→静态皮肤会停下，切回来恢复', (tester) async {
    await tester.pumpWidget(_host('neon'));
    expect(_state(tester).isTicking, isTrue);

    await tester.pumpWidget(_host('aegean'));
    expect(_state(tester).isTicking, isFalse);

    await tester.pumpWidget(_host('neon'));
    expect(_state(tester).isTicking, isTrue);
  });
}
