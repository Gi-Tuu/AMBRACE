import 'package:ambrace_pwa/main.dart' as app;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  tearDown(() => app.appLang.value = 'zh');

  testWidgets('首屏登录：zh 默认文案、切换 en、窄屏可用', (tester) async {
    tester.view.physicalSize = const Size(420, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const app.AmbracePwaApp());
    expect(find.text('拥爱 · 网页版'), findsOneWidget);
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('服务器地址'), findsOneWidget);

    // 语言切换（UI 文案 zh/en 成对，不是只在中文环境跑）
    await tester.tap(find.text('EN'));
    await tester.pumpAndSettle();
    expect(find.text('AMBRACE · Web'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);

    // 窄屏登录表单可渲染
    expect(find.byType(TextField), findsWidgets);
  });

  testWidgets('宽屏登录表单渲染（响应式，不单建第二套网页应用）', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const app.AmbracePwaApp());
    await tester.pumpAndSettle();
    expect(find.text('拥爱 · 网页版'), findsOneWidget);
    expect(find.text('配对'), findsOneWidget);
  });
}
