
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/widgets/shimmer.dart';

void main() {
  Widget wrap(Widget child) => MaterialApp(home: Scaffold(body: child));

  testWidgets('Shimmer 流光容器：ShaderMask 驱动动画持续推进', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const Shimmer(child: SkeletonBox(width: 120, height: 20))));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.byType(Shimmer), findsOneWidget);
    expect(find.byType(ShaderMask), findsOneWidget);
    // 无限 repeat 动画：仍有帧在跑，避免 pumpAndSettle 死循环应改用固定时长 pump
    expect(tester.hasRunningAnimations, isTrue);
  });

  testWidgets('AI 好友列表骨架：显示 shimmer 骨架而非全屏转圈', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const CharacterListSkeleton()));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.byType(Shimmer), findsWidgets);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(SkeletonCircle), findsWidgets);
    expect(find.byType(SkeletonLine), findsWidgets);
  });

  testWidgets('朋友圈/手机/小家三页骨架均使用 shimmer 且无转圈', (WidgetTester tester) async {
    await tester.pumpWidget(wrap(const MomentsSkeleton()));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byType(Shimmer), findsWidgets);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    await tester.pumpWidget(wrap(const AiInteractionSkeleton()));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byType(Shimmer), findsWidgets);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    await tester.pumpWidget(wrap(const HomeVisualSkeleton()));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byType(Shimmer), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(SkeletonBox), findsWidgets);
  });
}
